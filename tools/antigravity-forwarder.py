#!/usr/bin/env python3
import asyncio
import socket
import re
import os
import sys
import time
import argparse
import logging

LOG_PATH = os.path.expanduser("~/.config/Antigravity/logs/main.log")
DEFAULT_FORWARD_PORT = 18889

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

current_target_port = None

def detect_antigravity_port():
    """Detect the active dynamic port from Antigravity main.log and verify socket."""
    if not os.path.exists(LOG_PATH):
        return None
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(max(0, os.path.getsize(LOG_PATH) - 131072))
            content = f.read()
        matches = re.findall(r"https://127\.0\.0\.1:(\d+)/", content)
        if not matches:
            return None
        # Check from newest to oldest
        for p_str in reversed(matches):
            p = int(p_str)
            if not 1 <= p <= 65535:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", p)) == 0:
                    return p
    except Exception as e:
        logging.debug(f"Error checking port: {e}")
    return None

async def monitor_target_port():
    """Background task to continuously detect port changes."""
    global current_target_port
    while True:
        detected = detect_antigravity_port()
        if detected != current_target_port:
            if detected:
                logging.info(f"Target port changed: {current_target_port} -> {detected}")
            else:
                logging.warning("Antigravity dynamic port is currently offline or undetected.")
            current_target_port = detected
        await asyncio.sleep(1.0)

async def pipe(reader, writer):
    """Bidirectionally pipe bytes between client and backend."""
    try:
        while not reader.at_eof():
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        logging.debug(f"Pipe error: {e}")
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except Exception:
            pass

async def handle_client(client_reader, client_writer):
    global current_target_port
    if not current_target_port:
        # Try a direct detect right now
        current_target_port = detect_antigravity_port()

    if not current_target_port:
        logging.warning("Rejected incoming connection: target Antigravity port not ready")
        client_writer.close()
        return

    target = current_target_port
    try:
        remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", target)
    except Exception as e:
        logging.warning(f"Failed to connect to backend 127.0.0.1:{target}: {e}")
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    try:
        await asyncio.gather(pipe(client_reader, remote_writer), pipe(remote_reader, client_writer))
    finally:
        for writer in (remote_writer, client_writer):
            writer.close()
        await asyncio.gather(remote_writer.wait_closed(), client_writer.wait_closed(), return_exceptions=True)

async def main():
    global LOG_PATH
    parser = argparse.ArgumentParser(description="Antigravity Dynamic Port Forwarder")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_FORWARD_PORT, help="Fixed port to listen on")
    parser.add_argument("--listen-host", type=str, default="127.0.0.1", help="Host/IP to bind")
    parser.add_argument("--log-path", default=LOG_PATH, help="Antigravity main.log path")
    args = parser.parse_args()
    if not 1 <= args.listen_port <= 65535:
        parser.error('listen-port must be between 1 and 65535')
    LOG_PATH = args.log_path

    # Start monitor task
    asyncio.create_task(monitor_target_port())

    server = await asyncio.start_server(handle_client, args.listen_host, args.listen_port)
    logging.info(f"Antigravity Forwarder running on {args.listen_host}:{args.listen_port}")

    # Wait for first port detection
    for _ in range(5):
        detected = detect_antigravity_port()
        if detected:
            global current_target_port
            current_target_port = detected
            logging.info(f"Initial target port linked: 127.0.0.1:{current_target_port}")
            break
        await asyncio.sleep(0.5)

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Shutting down forwarder...")
