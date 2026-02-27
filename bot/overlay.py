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
        <title>Mockbot TTS - {{channel}}</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
        <style>
            :root {{
                --neon-cyan: #0ff;
                --neon-magenta: #f0f;
                --dark-bg: rgba(10, 10, 12, 0.85);
            }}
            body {{
                background-color: transparent;
                margin: 0;
                padding: 40px;
                font-family: 'Outfit', sans-serif;
                color: #e2e8f0;
                overflow: hidden;
            }}
            
            /* The main widget container starts hidden (translated down and faded out) */
            .cyber-widget {{
                position: absolute;
                bottom: 40px;
                left: 40px;
                background: var(--dark-bg);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(0, 255, 255, 0.1);
                border-left: 4px solid var(--neon-cyan);
                border-radius: 8px;
                padding: 16px 24px;
                display: flex;
                flex-direction: column;
                gap: 8px;
                width: 400px;
                box-shadow: 0 0 20px rgba(0, 255, 255, 0.1), inset 0 0 20px rgba(0, 0, 0, 0.5);
                
                opacity: 0;
                transform: translateY(20px);
                transition: all 0.5s cubic-bezier(0.19, 1, 0.22, 1);
            }}
            
            /* When active, the widget pops up and glows */
            .cyber-widget.active {{
                opacity: 1;
                transform: translateY(0);
                box-shadow: 0 0 30px rgba(0, 255, 255, 0.2), inset 0 0 20px rgba(0, 0, 0, 0.5);
            }}

            .header-row {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                font-size: 14px;
                color: var(--neon-cyan);
                text-transform: uppercase;
                letter-spacing: 2px;
                font-weight: 600;
            }}
            
            .bot-name {{
                display: flex;
                align-items: center;
                gap: 8px;
            }}

            /* The pulsing recording dot */
            .record-dot {{
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background-color: var(--neon-magenta);
                box-shadow: 0 0 10px var(--neon-magenta);
                animation: pulse 1.5s infinite;
            }}
            
            @keyframes pulse {{
                0% {{ opacity: 1; transform: scale(1); }}
                50% {{ opacity: 0.5; transform: scale(1.2); }}
                100% {{ opacity: 1; transform: scale(1); }}
            }}

            /* The typographic message body */
            .message-body {{
                font-size: 18px;
                font-weight: 300;
                line-height: 1.4;
                color: #f8fafc;
                min-height: 50px;
            }}
            
            /* Visualizer Animation */
            .visualizer {{
                display: flex;
                align-items: center;
                gap: 4px;
                height: 16px;
            }}
            .bar {{
                width: 3px;
                height: 100%;
                background-color: var(--neon-cyan);
                border-radius: 2px;
                animation: bounce 0.4s ease infinite alternate;
            }}
            .bar:nth-child(2) {{ animation-delay: 0.1s; background-color: var(--neon-magenta); }}
            .bar:nth-child(3) {{ animation-delay: 0.2s; }}
            .bar:nth-child(4) {{ animation-delay: 0.3s; background-color: var(--neon-magenta); }}
            .bar:nth-child(5) {{ animation-delay: 0.4s; }}
            
            @keyframes bounce {{
                0% {{ transform: scaleY(0.2); }}
                100% {{ transform: scaleY(1); }}
            }}
            
            /* Debug Toggle Container - hidden by default unless hovered for testing */
            .debug-container {{
                position: absolute;
                top: 20px;
                right: 20px;
                opacity: 0;
                transition: opacity 0.3s;
                background: rgba(0,0,0,0.5);
                padding: 10px;
                border-radius: 8px;
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 12px;
            }}
            body:hover .debug-container {{
                opacity: 1;
            }}
            input[type=checkbox] {{ accent-color: var(--neon-cyan); }}

        </style>
    </head>
    <body>
        <!-- The Main OBS Notification Widget -->
        <div class="cyber-widget" id="cyberWidget">
            <div class="header-row">
                <div class="bot-name">
                    <div class="record-dot"></div>
                    MOCKBOT_TTS_SYS
                </div>
                <div class="visualizer" id="visualizer">
                    <div class="bar"></div><div class="bar"></div>
                    <div class="bar"></div><div class="bar"></div>
                    <div class="bar"></div>
                </div>
            </div>
            <div class="message-body" id="messageBody">
                <!-- Text typed out here -->
            </div>
        </div>

        <!-- Hidden Debug Menu -->
        <div class="debug-container">
            <span id="statusText">Connecting...</span>
            <label>
                <input type="checkbox" id="ttsToggle" checked> Active
            </label>
        </div>

        <audio id="ttsAudioPlayer" style="display: none;"></audio>
        
        <script>
            let ws;
            const player = document.getElementById('ttsAudioPlayer');
            const statusText = document.getElementById('statusText');
            const ttsToggle = document.getElementById('ttsToggle');
            const cyberWidget = document.getElementById('cyberWidget');
            const messageBody = document.getElementById('messageBody');
            
            let typingInterval = null;

            player.onplay = () => {{
                // Audio started, keep widget active
            }};
            
            player.onended = () => {{
                // Audio finished, fade widget out
                setTimeout(() => {{ cyberWidget.classList.remove('active'); }}, 1000);
            }};
            
            // Typewriter effect function
            function typeString(str, speed=30) {{
                messageBody.innerHTML = '';
                if(typingInterval) clearInterval(typingInterval);
                
                let i = 0;
                cyberWidget.classList.add('active'); // Pop the widget up immediately
                
                typingInterval = setInterval(() => {{
                    if (i < str.length) {{
                        messageBody.innerHTML += str.charAt(i);
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
                    console.log("Connected to Mockbot Cyber-Noir TTS websocket.");
                    statusText.innerText = "Ready ({channel})";
                }};
                
                ws.onmessage = (event) => {{
                    try {{
                        const data = JSON.parse(event.data);
                        if (data.action === 'play_audio' && data.file) {{
                            if (ttsToggle.checked) {{
                                console.log("Incoming Message:", data.message);
                                
                                // Type out the transcribed text
                                if(data.message) {{
                                    typeString(data.message);
                                }} else {{
                                    typeString("<< AUDIO TRANSMISSION RECEIVED >>");
                                }}

                                // Play the audio file
                                player.src = data.file;
                                player.play().catch(e => {{
                                    console.error("Browser blocked autoplay:", e);
                                    statusText.innerText = "Autoplay blocked";
                                }});
                            }}
                        }}
                    }} catch (e) {{
                        console.error("Error parsing websocket message:", e);
                    }}
                }};
                
                ws.onclose = () => {{
                    console.log("Websocket disconnected. Reconnecting in 3s...");
                    statusText.innerText = "Reconnecting...";
                    setTimeout(connect, 3000);
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
