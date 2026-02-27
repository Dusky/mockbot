import logging
import json
import asyncio
from aiohttp import web
from pathlib import Path

# connected_clients maps a channel_name to a set of WebSocketResponse objects
connected_clients = {}

# Global reference to the main event loop
main_loop = None

async def serve_overlay(request):
    """Serve the static HTML for the OBS browser source."""
    channel = request.match_info.get('channel', '').lower()
    if not channel:
        return web.Response(text="Missing channel parameter.", status=400)

    # Note: Use WSS in production if behind HTTPS proxy, but WS is fine for localhost OBS source
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Mockbot TTS - {channel}</title>
        <style>
            body {{
                margin: 0;
                padding: 20px;
                font-family: -apple-system, BlinkMacMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #0f172a; /* Slate 900 */
                color: #f8fafc; /* Slate 50 */
                display: flex;
                flex-direction: column;
                gap: 16px;
                align-items: center;
            }}
            .widget-container {{
                background: #1e293b; /* Slate 800 */
                border-radius: 12px;
                padding: 20px 24px;
                display: flex;
                flex-direction: column;
                gap: 16px;
                box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
                width: 100%;
                max-width: 400px;
            }}
            .top-bar {{
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .left-section {{
                display: flex;
                flex-direction: column;
                gap: 4px;
            }}
            .title-row {{
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .status-dot {{
                width: 10px;
                height: 10px;
                border-radius: 50%;
                background-color: #ef4444; /* Red 500 - Disconnected */
                transition: background-color 0.3s;
            }}
            .status-dot.connected {{
                background-color: #22c55e; /* Green 500 */
            }}
            .title {{
                font-size: 1.1rem;
                font-weight: 600;
            }}
            .subtitle {{
                font-size: 0.85rem;
                color: #94a3b8; /* Slate 400 */
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            
            /* Visualizer */
            .visualizer {{
                display: none;
                align-items: center;
                gap: 3px;
                height: 14px;
            }}
            .visualizer.playing {{
                display: flex;
            }}
            .bar {{
                width: 3px;
                background-color: #38bdf8; /* Sky 400 */
                border-radius: 2px;
                animation: bounce 0.5s ease infinite alternate;
            }}
            .bar:nth-child(1) {{ height: 60%; animation-delay: 0.0s; }}
            .bar:nth-child(2) {{ height: 100%; animation-delay: 0.1s; background-color: #818cf8; }}
            .bar:nth-child(3) {{ height: 80%; animation-delay: 0.2s; }}
            .bar:nth-child(4) {{ height: 40%; animation-delay: 0.3s; background-color: #818cf8; }}
            
            @keyframes bounce {{
                from {{ transform: scaleY(0.5); }}
                to {{ transform: scaleY(1.0); }}
            }}
            
            /* Toggle Switch */
            .switch {{
                position: relative;
                display: inline-block;
                width: 44px;
                height: 24px;
            }}
            .switch input {{
                opacity: 0;
                width: 0;
                height: 0;
            }}
            .slider {{
                position: absolute;
                cursor: pointer;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background-color: #475569;
                transition: .4s;
                border-radius: 24px;
            }}
            .slider:before {{
                position: absolute;
                content: "";
                height: 18px;
                width: 18px;
                left: 3px;
                bottom: 3px;
                background-color: white;
                transition: .3s;
                border-radius: 50%;
            }}
            input:checked + .slider {{
                background-color: #6366f1;
            }}
            input:checked + .slider:before {{
                transform: translateX(20px);
            }}

            /* Transcript Area */
            .transcript-area {{
                background: #0f172a;
                border-radius: 8px;
                padding: 12px;
                font-size: 0.95rem;
                color: #cbd5e1;
                min-height: 40px;
                border: 1px solid #334155;
            }}
        </style>
    </head>
    <body>
        <div class="widget-container" id="widgetContainer">
            <div class="top-bar">
                <div class="left-section">
                    <div class="title-row">
                        <div class="status-dot" id="statusDot"></div>
                        <div class="title">Mockbot TTS Output</div>
                    </div>
                    <div class="subtitle">
                        <span id="statusText">Connecting...</span>
                        <div class="visualizer" id="visualizer">
                            <div class="bar"></div>
                            <div class="bar"></div>
                            <div class="bar"></div>
                            <div class="bar"></div>
                        </div>
                    </div>
                </div>
                
                <label class="switch" title="Toggle Auto-TTS Playback">
                    <input type="checkbox" id="ttsToggle" checked>
                    <span class="slider"></span>
                </label>
            </div>
            
            <div class="transcript-area" id="transcriptArea">
                Waiting for messages...
            </div>
        </div>

        <audio id="ttsAudioPlayer" style="display: none;"></audio>
        
        <script>
            let ws;
            const player = document.getElementById('ttsAudioPlayer');
            const statusDot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');
            const ttsToggle = document.getElementById('ttsToggle');
            const visualizer = document.getElementById('visualizer');
            const transcriptArea = document.getElementById('transcriptArea');
            
            let typingInterval = null;

            player.onplay = () => {{
                visualizer.classList.add('playing');
                statusText.innerText = "Playing audio...";
            }};
            
            player.onended = () => {{
                visualizer.classList.remove('playing');
                statusText.innerText = "Ready ({channel})";
            }};
            
            function typeString(str, speed=30) {{
                transcriptArea.innerHTML = '';
                if(typingInterval) clearInterval(typingInterval);
                
                let i = 0;
                typingInterval = setInterval(() => {{
                    if (i < str.length) {{
                        transcriptArea.innerHTML += str.charAt(i);
                        i++;
                    }} else {{
                        clearInterval(typingInterval);
                    }}
                }}, speed);
            }}

            function connect() {{
                const wsUrl = `ws://${{window.location.host}}/ws/{channel}`;
                ws = new WebSocket(wsUrl);
                
                ws.onopen = () => {{
                    console.log("Connected to Mockbot TTS Overlay websocket.");
                    statusDot.classList.add('connected');
                    statusText.innerText = "Ready ({channel})";
                }};
                
                ws.onmessage = (event) => {{
                    try {{
                        const data = JSON.parse(event.data);
                        if (data.action === 'play_audio' && data.file) {{
                            if (ttsToggle.checked) {{
                                console.log("Playing audio:", data.file);
                                
                                if(data.message) {{
                                    typeString(data.message);
                                }} else {{
                                    typeString("<< AUDIO TRANSMISSION RECEIVED >>");
                                }}
                                
                                player.src = data.file;
                                player.play().catch(e => {{
                                    console.error("Error expected if no user interaction:", e);
                                    statusText.innerText = "Browser blocked autoplay";
                                }});
                            }} else {{
                                console.log("Audio received but toggle is off.");
                            }}
                        }}
                    }} catch (e) {{
                        console.error("Error parsing websocket message:", e);
                    }}
                }};
                
                ws.onclose = () => {{
                    console.log("Websocket disconnected. Reconnecting in 3s...");
                    statusDot.classList.remove('connected');
                    statusText.innerText = "Reconnecting...";
                    setTimeout(connect, 3000);
                }};
                
                ws.onerror = (error) => {{
                    console.error("Websocket error:", error);
                }};
            }}
            
            // Connect immediately
            connect();
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

async def websocket_handler(request):
    """Handle incoming WebSocket connections from OBS overlays."""
    channel = request.match_info.get('channel', '').lower()
    if not channel:
        return web.Response(text="Missing channel parameter.", status=400)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    if channel not in connected_clients:
        connected_clients[channel] = set()
    connected_clients[channel].add(ws)
    
    logging.info(f"WebSocket client connected to {channel} overlay.")

    try:
        async for msg in ws:
            # We don't expect messages from the client overlay, but we must pump the loop
            pass
    finally:
        connected_clients[channel].discard(ws)
        if not connected_clients[channel]:
            del connected_clients[channel]
        logging.info(f"WebSocket client disconnected from {channel} overlay.")

    return ws

def broadcast_audio(channel: str, file_path: str, message: str = ""):
    """
    Called by the TTS thread to notify all connected overlays to play a file.
    Note: Can be called from a synchronous thread, so we schedule the async broadcast.
    """
    global main_loop
    clean_channel = channel.lstrip('#').lower()
    
    # Static files are mounted at /audio/, so we take everything from static/outputs onwards
    # e.g static/outputs/firestarman/firestarman-1234.wav -> /audio/firestarman/firestarman-1234.wav
    # The 'static/outputs' path must match the static route mount point below.
    try:
        rel_path = str(Path(file_path).relative_to("static/outputs"))
        audio_url = f"/audio/{rel_path}"
    except ValueError:
        logging.error(f"Cannot broadcast file outside of static/outputs: {file_path}")
        return

    payload = json.dumps({
        "action": "play_audio",
        "file": audio_url,
        "message": message
    })

    if clean_channel in connected_clients and main_loop is not None:
        # Create tasks to send to all clients
        for ws in connected_clients[clean_channel]:
            try:
                # We use asyncio.run_coroutine_threadsafe to fire from the background thread
                asyncio.run_coroutine_threadsafe(ws.send_str(payload), main_loop)
            except Exception as e:
                logging.error(f"Failed to send WS message to overlay: {e}")
    elif main_loop is None:
        logging.warning("Cannot broadcast_audio: main_loop is not active.")

async def start_server(host='0.0.0.0', port=5050):
    """Start the aiohttp web server."""
    global main_loop
    main_loop = asyncio.get_event_loop()
    
    app = web.Application()
    
    # Routes
    app.router.add_get('/overlay/{channel}', serve_overlay)
    app.router.add_get('/ws/{channel}', websocket_handler)
    
    # Mount static files directly. 
    # Mockbot TTS outputs save to static/outputs/<channel>/<file>.wav
    app.router.add_static('/audio/', path='static/outputs', name='audio')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logging.info(f"TTS Overlay Server started at http://localhost:{port}/overlay/<channel>")
    return runner
