# forklift field guide

An interactive nine-lesson companion to Forklift. Open `dist/index.html` through
an HTTP server; source-file reading requires HTTP fetch. No real database is
connected. All simulations are labeled, and benchmark values come from the
committed milestone-five reports. Quiz progress is saved only in browser storage.

```sh
python3 -m http.server 4381 --directory learning-site/dist
```

The published site is private:
https://forklift-field-guide.alanagoyal.chatgpt.site

`dist/sources/snapshot.json` records the repository revision used for the source
snapshot. Update this snapshot and the lesson text together when the engine
changes. Source links open a readable verbatim file viewer. The website does not
launch PostgreSQL, run SQL, or modify any stores.

Implementation: static HTML, CSS, and JavaScript with no build or runtime
packages. Fonts use Google Fonts with system fallbacks. `lessons.js` contains
lesson content and models; `app.js` handles navigation, progress, and quizzes.
Optional browser WebMCP tools expose lesson navigation and reading progress;
unsupported browsers use the complete normal interface.

Publication uses a separate Sites checkout so the Forklift repository's Git
history and remote are unchanged. The ignored `.openai/hosting.json` retains the local site identity.
