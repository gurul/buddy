<!-- ERA:MANAGED:START -->
Always make sure that the /docs folder stays up to date (and the README.md) you can find the structure for the docs in the skills folder when you need it.
Always ask for explicit permission before pushing any changes.

### RTK and curl
RTK compresses shell output to save tokens, but `rtk curl` replaces JSON values with type summaries (e.g. `"name": "<string>"`), which breaks `jq` and other JSON processors.
The RTK plugin automatically skips rewriting when it detects `curl` piped to `jq`, `python`, `node`, or similar tools, or when output is redirected to a file.
If you need raw JSON from curl for any other reason, use `/usr/bin/curl` to bypass RTK entirely.
<!-- ERA:MANAGED:END -->