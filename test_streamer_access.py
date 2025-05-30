#!/usr/bin/env python3
"""
Test script to verify streamer route access restrictions
"""

import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

def test_routes_with_streamer_redirects():
    """Check which routes have streamer redirect logic"""
    
    routes_to_check = [
        ('/', 'main dashboard'),
        ('/settings', 'settings page'),
        ('/stats', 'statistics page'),
        ('/logs', 'logs page'),
        ('/bot-control', 'bot control page'),
        ('/tts-history', 'TTS history page'),
        ('/channel/<channel_name>', 'old channel interface'),
        ('/beta', 'beta dashboard'),
        ('/beta/settings', 'beta settings'),
    ]
    
    print("🔍 Routes with streamer access restrictions:")
    print("=" * 60)
    
    # Read webapp.py and check for streamer redirect logic
    with open('webapp.py', 'r') as f:
        content = f.read()
    
    for route, description in routes_to_check:
        # Find the route definition
        route_pattern = route.replace('<channel_name>', '.*')
        if f"@app.route('{route}')" in content or f'@app.route("{route}")' in content:
            # Check if it has streamer redirect logic
            route_start = content.find(f"@app.route('{route}')")
            if route_start == -1:
                route_start = content.find(f'@app.route("{route}")')
            
            if route_start != -1:
                # Get the next 500 characters to check for redirect logic
                route_section = content[route_start:route_start + 1000]
                
                if 'redirect_streamers_to_channel' in route_section or 'streamer' in route_section.lower():
                    print(f"✅ {route:<25} - {description} (PROTECTED)")
                else:
                    print(f"❌ {route:<25} - {description} (NOT PROTECTED)")
            else:
                print(f"❓ {route:<25} - {description} (ROUTE NOT FOUND)")
        else:
            print(f"❓ {route:<25} - {description} (ROUTE NOT FOUND)")

if __name__ == "__main__":
    print("🧪 Testing streamer access restrictions...")
    test_routes_with_streamer_redirects()
    
    print("\n🎯 Expected behavior for streamers:")
    print("- Should be redirected to /beta/channel/skip_skipperson from all protected routes")
    print("- Should NOT be able to access settings, stats, logs, bot-control, etc.")
    print("- Should only have access to their assigned channel page")