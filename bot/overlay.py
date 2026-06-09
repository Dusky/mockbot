import logging
import json
import asyncio
from aiohttp import web
from pathlib import Path

# connected_clients maps a channel_name to a set of WebSocketResponse objects
connected_clients = {}

# Global reference to the main event loop
main_loop = None

# Database instance injected by Bot.__init__ via init_overlay_db()
_database = None


def set_overlay_db(db_file: str) -> None:
    """Legacy shim — prefer init_overlay_db(database)."""
    from bot.database import Database
    global _database
    _database = Database(db_file)


def init_overlay_db(database) -> None:
    global _database
    _database = database

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
                margin: 0; padding: 20px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                color: #f8fafc;
                display: flex; flex-direction: column; gap: 16px; align-items: flex-start;
                background: transparent; /* Essential for OBS transparent background */
            }}
            .main-hud {{
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(10px);
                border-radius: 12px;
                padding: 16px 20px;
                box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
                width: 100%;
                max-width: 450px;
                display: flex;
                flex-direction: column;
                gap: 12px;
                border: 1px solid rgba(51, 65, 85, 0.5);
                transition: opacity 0.5s ease-in-out, transform 0.5s ease-in-out;
                opacity: 0;
                transform: translateY(20px);
            }}
            .main-hud.active {{
                opacity: 1;
                transform: translateY(0);
            }}
            .hud-header {{
                display: flex; justify-content: space-between; align-items: center;
                border-bottom: 1px solid rgba(71, 85, 105, 0.5);
                padding-bottom: 8px;
            }}
            .hud-title {{ font-size: 0.9rem; font-weight: 600; color: #94a3b8; display: flex; align-items: center; gap: 8px; }}
            .status-dot {{ width: 8px; height: 8px; border-radius: 50%; background-color: #ef4444; }}
            .status-dot.connected {{ background-color: #22c55e; }}
            
            /* Visualizer */
            .visualizer {{ display: flex; align-items: center; gap: 3px; height: 14px; opacity: 0; transition: opacity 0.2s; }}
            .visualizer.playing {{ opacity: 1; }}
            .bar {{ width: 3px; background-color: #38bdf8; border-radius: 2px; animation: bounce 0.5s ease infinite alternate; }}
            .bar:nth-child(1) {{ height: 60%; animation-delay: 0.0s; }}
            .bar:nth-child(2) {{ height: 100%; animation-delay: 0.1s; background-color: #818cf8; }}
            .bar:nth-child(3) {{ height: 80%; animation-delay: 0.2s; }}
            .bar:nth-child(4) {{ height: 40%; animation-delay: 0.3s; background-color: #818cf8; }}
            @keyframes bounce {{ from {{ transform: scaleY(0.4); }} to {{ transform: scaleY(1.0); }} }}

            /* Chat Container */
            .chat-container {{
                display: flex;
                flex-direction: column;
                gap: 12px;
            }}
            .message-bubble {{
                background: rgba(30, 41, 59, 0.8);
                border-radius: 8px;
                padding: 12px;
                transition: all 0.4s ease;
                border-left: 3px solid #6366f1;
            }}
            .message-bubble.new {{
                opacity: 0;
                transform: translateX(-10px);
            }}
            .message-bubble.fade-out {{
                opacity: 0;
                transform: scale(0.95);
            }}
            .msg-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 6px;
            }}
            .msg-author {{
                font-size: 0.95rem;
                font-weight: 700;
                color: #e2e8f0;
            }}
            .msg-badges {{
                display: flex;
                gap: 6px;
            }}
            .badge-provider {{
                background: #312e81; /* Indigo 900 */
                color: #a5b4fc; /* Indigo 300 */
                font-size: 0.7rem;
                padding: 2px 6px;
                border-radius: 4px;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .badge-voice {{
                background: #0f766e; /* Teal 700 */
                color: #5eead4; /* Teal 300 */
                font-size: 0.7rem;
                padding: 2px 6px;
                border-radius: 4px;
                font-weight: 600;
            }}
            .msg-text {{
                font-size: 1.05rem;
                line-height: 1.4;
                color: #f1f5f9;
            }}
        </style>
    </head>
    <body>
        <div class="main-hud" id="hud">
            <div class="hud-header">
                <div class="hud-title">
                    <div class="status-dot" id="statusDot"></div>
                    <span>TTS Broadcast ({channel})</span>
                </div>
                <div class="visualizer" id="visualizer">
                    <div class="bar"></div><div class="bar"></div><div class="bar"></div><div class="bar"></div>
                </div>
            </div>
            
            <div class="chat-container" id="chatContainer">
                <!-- Messages spawn here -->
            </div>
        </div>

        <audio id="ttsAudioPlayer" style="display: none;"></audio>
        
        <script>
            let ws;
            const player = document.getElementById('ttsAudioPlayer');
            const hud = document.getElementById('hud');
            const chatContainer = document.getElementById('chatContainer');
            const visualizer = document.getElementById('visualizer');
            const statusDot = document.getElementById('statusDot');
            
            let hideHudTimeout = null;

            player.onplay = () => {{
                visualizer.classList.add('playing');
                if(hideHudTimeout) clearTimeout(hideHudTimeout);
            }};
            
            player.onended = () => {{
                visualizer.classList.remove('playing');
                resetHudTimeout();
            }};
            
            function resetHudTimeout() {{
                if(hideHudTimeout) clearTimeout(hideHudTimeout);
                hideHudTimeout = setTimeout(() => {{
                    if (player.paused && chatContainer.children.length === 0) {{
                        hud.classList.remove('active');
                    }}
                }}, 10000); // Sleep HUD after 10s idle
            }}

            function appendMessage(text, author, provider, voice) {{
                // Wake up HUD
                hud.classList.add('active');
                if(hideHudTimeout) clearTimeout(hideHudTimeout);
                
                const msgDiv = document.createElement('div');
                msgDiv.className = 'message-bubble new';
                
                let headerHtml = `<div class="msg-header">`;
                headerHtml += `<div class="msg-author">${{author || 'Anonymous'}}</div>`;
                headerHtml += `<div class="msg-badges">`;
                if(provider) headerHtml += `<span class="badge-provider">${{provider}}</span>`;
                if(voice) headerHtml += `<span class="badge-voice">${{voice}}</span>`;
                headerHtml += `</div></div>`;
                
                msgDiv.innerHTML = headerHtml + `<div class="msg-text">${{text}}</div>`;
                chatContainer.appendChild(msgDiv);
                
                // Trigger CSS transition
                requestAnimationFrame(() => {{
                    msgDiv.classList.remove('new');
                }});
                
                // Enforce max 3 messages
                while (chatContainer.children.length > 3) {{
                    chatContainer.removeChild(chatContainer.firstChild);
                }}
                
                // Auto-delete message bubble after 15 seconds
                setTimeout(() => {{
                    msgDiv.classList.add('fade-out');
                    setTimeout(() => {{
                        if (msgDiv.parentElement) msgDiv.remove();
                        resetHudTimeout(); // Check if HUD should sleep
                    }}, 400);
                }}, 15000);
            }}

            function connect() {{
                ws = new WebSocket(`ws://${{window.location.host}}/ws/{channel}`);
                ws.onopen = () => {{
                    statusDot.classList.add('connected');
                    appendMessage("OBS Overlay Connected and Listening...", "SYSTEM", "MOCKBOT", "");
                }};
                ws.onclose = () => {{
                    statusDot.classList.remove('connected');
                    setTimeout(connect, 3000);
                }};
                
                ws.onmessage = (event) => {{
                    try {{
                        const data = JSON.parse(event.data);
                        if (data.action === 'kill_audio') {{
                            player.pause(); player.src = '';
                            visualizer.classList.remove('playing');
                            chatContainer.innerHTML = ''; 
                            resetHudTimeout();
                        }}
                        
                        if (data.action === 'play_audio' && data.file) {{
                            appendMessage(data.message || '🎙️ << audio transmission >>', data.author, data.provider, data.voice);
                            player.src = data.file;
                            player.play().catch(e => console.error("Autoplay blocked:", e));
                        }}
                    }} catch (e) {{ console.error("WS parse error:", e); }}
                }};
            }}
            
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

def broadcast_audio(channel: str, file_path: str, message: str = "", provider: str = "", voice: str = "", author: str = ""):
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
        "message": message,
        "provider": provider,
        "voice": voice,
        "author": author
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

async def api_get_variables(request):
    """Serve the active channel variables from the database as JSON."""
    channel = request.match_info.get('channel', '').lower()
    if not channel:
        return web.json_response({"error": "Missing channel parameter"}, status=400)
        
    if _database is None:
        return web.json_response({"error": "Database not initialized"}, status=500)
    try:
        variables = await _database.get_all_variables(channel)
    except Exception as e:
        logging.error(f"Error fetching variables for overlay API: {e}")
        return web.json_response({"error": "Database error"}, status=500)
        
    return web.json_response(variables)

async def start_server(host='0.0.0.0', port=5050):
    """Start the aiohttp web server."""
    global main_loop
    main_loop = asyncio.get_event_loop()
    
    app = web.Application()
    
    # Routes
    app.router.add_get('/overlay/{channel}', serve_overlay)
    app.router.add_get('/ws/{channel}', websocket_handler)
    app.router.add_get('/api/variables/{channel}', api_get_variables)
    
    # Mount static files directly. 
    # Mockbot TTS outputs save to static/outputs/<channel>/<file>.wav
    app.router.add_static('/audio/', path='static/outputs', name='audio')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logging.info(f"TTS Overlay Server started at http://localhost:{port}/overlay/<channel>")
    return runner

def broadcast_kill_audio():
    """Called by TTS system's kill switch to instantly stop any playing audio on all connected overlays."""
    global main_loop
    payload = json.dumps({"action": "kill_audio"})
    
    if main_loop is not None:
        for channel_ws_set in connected_clients.values():
            for ws in channel_ws_set:
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_str(payload), main_loop)
                except Exception as e:
                    logging.error(f"Failed to send kill message to overlay: {e}")
