This revision fixes Viewer lifecycle.

Behavior:
- Fixed Viewer port: 8766. It does NOT silently move to 8767/8768.
- Before every Viewer start, the previous Numbers Diary Viewer is stopped.
- The Viewer writes viewer/.numbers_diary_viewer.pid.
- After a Jupyter kernel restart, the helper can still stop the stale Viewer by PID.
- macOS fallback uses lsof + ps and only terminates a listener when its command
  identifies this Numbers Diary viewer/app.py. It will not kill an unrelated app.
- Browser open uses a cache-busting query so each run creates a fresh navigation.
- Notebook imports the helper with importlib.reload(), so editing/replacing the .py
  does not require restarting the Jupyter kernel.
- Default output root is now ~/Downloads/Diary Export Test.

Notebook remains 4 cells. Cell 4 remains:
    display_numbers_export(result)

Manual stop, if wanted:
    stop_numbers_viewer()
