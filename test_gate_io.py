#!/usr/bin/env python3
"""Test script to verify Gate.io is being used as primary data source."""

import os
import sys
import time
import json
import requests

API_KEY = os.environ.get("VIBE_API_KEY") or os.environ.get("API_AUTH_KEY")
BASE_URL = os.environ.get("VIBE_BASE_URL", "http://localhost:8899")

def create_session():
    """Create a new session."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    response = requests.post(f"{BASE_URL}/sessions", headers=headers, data="{}")
    if response.status_code in [200, 201]:
        return response.json()["session_id"]
    print(f"Failed to create session: {response.status_code} - {response.text}")
    return None

def send_message(session_id, content):
    """Send a message to the session."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {"content": content}
    response = requests.post(f"{BASE_URL}/sessions/{session_id}/messages", headers=headers, json=data)
    if response.status_code == 200:
        result = response.json()
        return result.get("message_id"), result.get("linked_attempt_id")
    print(f"Failed to send message: {response.status_code} - {response.text}")
    return None, None

def get_messages(session_id):
    """Get all messages from the session."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/sessions/{session_id}/messages", headers=headers)
    if response.status_code == 200:
        return response.json()
    print(f"Failed to get messages: {response.status_code} - {response.text}")
    return None

def main():
    if not API_KEY:
        print("Missing VIBE_API_KEY or API_AUTH_KEY environment variable")
        sys.exit(1)

    print("=== Testing Gate.io Data Source ===")
    
    # Create session
    session_id = create_session()
    if not session_id:
        sys.exit(1)
    print(f"✓ Session created: {session_id}")
    
    # Send test message
    message_id, attempt_id = send_message(session_id, "What is the current BTC price? Show me which data source you're using.")
    if not message_id:
        sys.exit(1)
    print(f"✓ Message sent: {message_id}")
    
    # Wait for response
    print("\nWaiting for Agent response...")
    for i in range(30):
        messages = get_messages(session_id)
        if messages:
            # Find the assistant response
            for msg in messages:
                if msg["role"] == "assistant" and msg.get("linked_attempt_id") == attempt_id:
                    content = msg["content"]
                    print("\n=== Agent Response ===")
                    print(content)
                    print("\n=== Analysis ===")
                    
                    # Check for Gate.io references
                    has_gate_io = "Gate.io" in content or "Gate.io" in content
                    has_ccxt_gate = "CCXT (gate)" in content or "CCXT (Gate)" in content
                    has_okx_via = "via OKX" in content or "OKX/Binance" in content
                    
                    if has_gate_io or has_ccxt_gate:
                        print("✅ SUCCESS: Gate.io is being used as the data source!")
                        if has_gate_io:
                            print("   - Found 'Gate.io' in response")
                        if has_ccxt_gate:
                            print("   - Found 'CCXT (gate)' in response")
                    elif has_okx_via:
                        print("❌ FAIL: Agent is still using OKX/Binance instead of Gate.io")
                        print("   - Found 'via OKX' or 'OKX/Binance' in response")
                    else:
                        print("⚠️  UNCLEAR: Could not determine data source from response")
                    
                    return
        
        time.sleep(2)
        print(f"Waiting... ({i+1}/30)")
    
    print("\n❌ Timeout waiting for response")

if __name__ == "__main__":
    main()
