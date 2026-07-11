r"""Launch wrapper for the control-panel Streamlit app.

Applies logging filters that suppress Windows networking noise before
Streamlit's server starts, then delegates to Streamlit's CLI.

Use via launch_app.bat or directly:
    .venv\Scripts\python.exe run_app.py
"""

from __future__ import annotations

import logging
import sys


class _SuppressConnReset(logging.Filter):
    """Drop ConnectionResetError tracebacks from asyncio's ProactorEventLoop.

    On Windows, the ProactorEventLoop logs a full traceback for every browser
    disconnect ([WinError 10054]). These are harmless; suppress them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ei = record.exc_info
        if ei and ei[0] and issubclass(ei[0], (ConnectionResetError, BrokenPipeError)):
            return False
        return True


# Suppress "Invalid HTTP request received" — a warning triggered by browser
# preconnects and non-HTTP traffic; not an app error. Streamlit >=1.5x serves
# over Starlette/uvicorn (not Tornado), so the real logger is "uvicorn.error";
# the "tornado.general" filter is kept for older Streamlit installs.
_no_invalid_http = type("_NoInvalidHTTP", (logging.Filter,), {
    "filter": staticmethod(lambda r: "Invalid HTTP request" not in r.getMessage())
})()
logging.getLogger("tornado.general").addFilter(_no_invalid_http)
logging.getLogger("uvicorn.error").addFilter(_no_invalid_http)
# Suppress ConnectionResetError / BrokenPipeError tracebacks from asyncio callbacks.
logging.getLogger("asyncio").addFilter(_SuppressConnReset())

sys.argv = ["streamlit", "run", "app/app.py", "--browser.gatherUsageStats=false"]

from streamlit.web import cli as stcli  # noqa: E402

stcli.main(standalone_mode=False)
