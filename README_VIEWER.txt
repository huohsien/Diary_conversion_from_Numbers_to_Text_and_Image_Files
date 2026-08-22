Files to place in the repository root:

Numbers_Diary_Parser_v1.ipynb
Numbers_Diary_Parser_v1.py
viewer/
    app.py
    templates/viewer.html
    static/style.css

Run the Notebook from top to bottom.

The fourth cell remains exactly:

    display_numbers_export(result)

That function now follows the Telegram Viewer pattern:
it starts viewer/app.py with subprocess.Popen(), waits for Flask to answer,
then opens http://127.0.0.1:8766/ in the default browser.

If needed, stop it from any notebook cell with:

    stop_numbers_viewer()
