import os
import json
import base64
import asyncio
import websockets
import requests
import platform
import uuid
import logging
from pathlib import Path

# Import constants
from .constants import (
    ServerResponse, Front2Edge, Edge2Agent, Agent2Edge, Edge2Front,
    SR, FE, EA, AE, EF
)
from .utils import generate_machine_fingerprint

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("todo4ai-client")

# Import handlers
from .handlers import (
    handle_todo_dir_list,
    handle_todo_cd,
    handle_block_execute,
    handle_block_save,
    handle_block_refresh,
    handle_block_keyboard,
    handle_block_signal,
    handle_block_diff,
    handle_task_action_new,
    handle_ctx_julia_request,
    handle_ctx_workspace_request,
    handle_diff_file_request
)
class EdgeConfig:
    """Edge configuration class"""
    def __init__(self, data=None):
        data = data or {}
        self.id = data.get("id", "")
        self.name = data.get("name", "Unknown Edge")
        self.workspacepaths = data.get("workspacepaths", [])
        self.owner_id = data.get("ownerId", "")
        self.status = data.get("status", "OFFLINE")
        self.is_shell_enabled = data.get("isShellEnabled", False)
        self.is_filesystem_enabled = data.get("isFileSystemEnabled", False)
        self.created_at = data.get("createdAt", None)

class Todo4AIClient:
    def __init__(self, api_url=None, api_key=None, debug=False):
        self.api_url = api_url or os.environ.get("TODO4AI_API_URL", "http://localhost:4000")
        self.api_key = api_key or os.environ.get("TODO4AI_API_KEY", "")
        self.debug = debug
        self.agent_id = ""
        self.user_id = ""
        self.edge_id = ""
        self.connected = False
        self.ws = None
        self.ws_url = self._api_to_ws_url(self.api_url)
        self.heartbeat_task = None
        self.config = EdgeConfig()
        self.fingerprint = generate_machine_fingerprint()

    def _api_to_ws_url(self, api_url):
        """Convert HTTP URL to WebSocket URL"""
        if api_url.startswith("https://"):
            return api_url.replace("https://", f"wss://") + f"/ws/v1/edge"
        else:
            return api_url.replace("http://", f"ws://") + f"/ws/v1/edge"

    async def _send_heartbeat(self):
        """Send periodic heartbeats to the server"""
        while self.connected:
            try:
                if self.agent_id:
                    if self.debug:
                        logger.debug(f"Sending heartbeat for agent {self.agent_id}")
                    
                    headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
                    url = f"{self.api_url}/api/v1/agents/{self.agent_id}/heartbeat"
                    requests.post(url, headers=headers, json={})
            except Exception as error:
                logger.error(f"Heartbeat error: {str(error)}")
            
            await asyncio.sleep(30)  # Send heartbeat every 30 seconds

    async def _handle_message(self, message):
        """Process incoming messages from the server"""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            payload = data.get("payload", {})
            
            if self.debug:
                logger.info(f"Received message type: {msg_type}")
                
            if msg_type == SR.CONNECTED_EDGE:
                self.edge_id = payload.get("edgeId", "")
                self.user_id = payload.get("userId", "")
                logger.info(f"Connected with edge ID: {self.edge_id} and user ID: {self.user_id}")
                
            elif msg_type == FE.EDGE_DIR_LIST:
                await handle_todo_dir_list(payload, self)
                
            elif msg_type == FE.EDGE_CD:
                await handle_todo_cd(payload, self)
                
            elif msg_type == FE.BLOCK_EXECUTE:
                await handle_block_execute(payload, self)
                
            elif msg_type == FE.BLOCK_SAVE:
                await handle_block_save(payload, self)
                
            elif msg_type == FE.BLOCK_REFRESH:
                await handle_block_refresh(payload, self)
                
            elif msg_type == FE.BLOCK_KEYBOARD:
                await handle_block_keyboard(payload, self)
                
            elif msg_type == FE.BLOCK_SIGNAL:
                await handle_block_signal(payload, self)
                
            elif msg_type == FE.BLOCK_DIFF:
                await handle_block_diff(payload, self)
                
            elif msg_type == FE.TASK_ACTION_NEW:
                await handle_task_action_new(payload, self)
                
            elif msg_type == AE.CTX_JULIA_REQUEST:
                await handle_ctx_julia_request(payload, self)
                
            elif msg_type == AE.CTX_WORKSPACE_REQUEST:
                await handle_ctx_workspace_request(payload, self)
                
            elif msg_type == AE.DIFF_FILE_REQUEST:
                await handle_diff_file_request(payload, self)
                
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except Exception as error:
            logger.error(f"Error handling message: {str(error)}")


    async def _send_response(self, channel, payload):
        """Send a response to the server"""
        if self.ws and self.connected:
            message = json.dumps({"type": channel, "payload": payload})
            await self.ws.send(message)
            if self.debug:
                logger.debug(f"Sent response: {channel}")

    async def connect(self):
        """Connect to the WebSocket server"""
        fingerprint = generate_machine_fingerprint()
        print(f"Fingerprint: {fingerprint}")
        ws_url = f"{self.ws_url}?apiKey={self.api_key}&fingerprint={fingerprint}"
        
        if self.debug:
            logger.info(f"Connecting to WebSocket: {ws_url}")
        
        try:
            async with websockets.connect(ws_url) as ws:
                self.ws = ws
                self.connected = True
                logger.info("WebSocket connected")
                
                # Start heartbeat task
                self.heartbeat_task = asyncio.create_task(self._send_heartbeat())
                
                # Process messages
                async for message in ws:
                    await self._handle_message(message)
                    
        except websockets.exceptions.InvalidStatusCode as error:
            logger.error(f"WebSocket connection failed with status code: {error.status_code}")
            if error.status_code == 401:
                logger.error("Authentication failed. Please check your API key.")
            elif error.status_code == 403:
                logger.error("Access forbidden. Your API key might not have the required permissions.")
            else:
                logger.error(f"Server returned error: {error}")
        except websockets.exceptions.ConnectionClosedError as error:
            logger.error(f"WebSocket connection closed unexpectedly: {error}")
        except websockets.exceptions.ConnectionClosedOK as error:
            logger.info(f"WebSocket connection closed normally: {error}")
        except Exception as error:
            logger.error(f"WebSocket connection error: {str(error)}")
        finally:
            self.connected = False
            self.ws = None
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                self.heartbeat_task = None
            logger.info("WebSocket disconnected")

    async def start(self):
        """Start the client with reconnection logic"""
        max_attempts = 20
        attempt = 0
        
        while attempt < max_attempts:
            logger.info(f"Connecting to server (attempt {attempt+1}/{max_attempts})")
            
            try:
                await self.connect()
                
                # If we get here, the connection was closed normally
                # Reset attempt counter
                attempt = 0
                
                # Wait before reconnecting
                logger.info("Connection closed. Reconnecting in 4 seconds...")
                await asyncio.sleep(4.0)
                
            except Exception as error:
                logger.error(f"Connection error: {str(error)}")
                attempt += 1
                
                if attempt < max_attempts:
                    delay = min(4 + attempt, 20.0)
                    logger.info(f"Reconnecting in {delay:.1f} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error("Maximum reconnection attempts reached. Giving up.")
                    break
