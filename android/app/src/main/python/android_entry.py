import threading


_server_started = False
_server_lock = threading.Lock()


def start_server():
    global _server_started
    with _server_lock:
        if _server_started:
            return
        _server_started = True

    def run():
        import uvicorn
        from web_server import app

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=9023,
            log_level="warning",
            access_log=False,
        )
        uvicorn.Server(config).run()

    thread = threading.Thread(target=run, name="qwen-web-server", daemon=True)
    thread.start()
