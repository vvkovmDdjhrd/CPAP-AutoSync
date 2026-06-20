try:
    Import("env")
except NameError:
    pass

import gzip
import os
import json
import re
import sys
import glob


def _add_project_venv_to_path():
    candidates = (
        glob.glob("venv/lib/python*/site-packages")
        + glob.glob("venv/Lib/site-packages")
    )
    for sp in candidates:
        absp = os.path.abspath(sp)
        if absp not in sys.path:
            sys.path.insert(0, absp)


_add_project_venv_to_path()

try:
    import minify_html as _mhtml
    _HAVE_MINIFY_HTML = True
except ImportError:
    _HAVE_MINIFY_HTML = False


_PROTECTED_RE = re.compile(
    r"(<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|"
    r"<pre\b[^>]*>.*?</pre>|<textarea\b[^>]*>.*?</textarea>)",
    re.IGNORECASE | re.DOTALL,
)


def _fallback_minify_outside(s):
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r">\s+<", "><", s)
    return s


def _fallback_minify_style(s):
    head, _, body = s.partition(">")
    body, _, tail = body.rpartition("</style>")
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    body = re.sub(r"\s+", " ", body).strip()
    return head + ">" + body + "</style>" + tail


def _fallback_minify_script(s):
    head, _, body = s.partition(">")
    body, _, tail = body.rpartition("</script>")
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return head + ">" + body + "</script>" + tail


def _fallback_minify_html(html):
    parts = _PROTECTED_RE.split(html)
    out = []
    for p in parts:
        low = p[:9].lower()
        if low.startswith("<script"):
            out.append(_fallback_minify_script(p))
        elif low.startswith("<style"):
            out.append(_fallback_minify_style(p))
        elif low.startswith("<pre") or low.startswith("<textarea"):
            out.append(p)
        else:
            out.append(_fallback_minify_outside(p))
    return "".join(out)


def minify_html(html):
    if _HAVE_MINIFY_HTML:
        return _mhtml.minify(
            html,
            minify_css=True,
            minify_js=True,
            keep_html_and_head_opening_tags=True,
        )
    return _fallback_minify_html(html)


def fetch_timezone_data():
    """Download zones.json from GitHub and convert to a JS object literal.
    Returns the JS object string, or '{}' if the download fails.
    zones.json format: { "Region/City": "POSIX_STRING", ... }
    This is simpler and more robust than CSV (no regex, no quoting edge-cases).
    """
    try:
        import urllib.request
        url = "https://raw.githubusercontent.com/nayarsystems/posix_tz_db/master/zones.json"
        print(f"  Fetching timezone data from {url}...")
        req = urllib.request.Request(url, headers={"User-Agent": "CPAP-AutoSync-Build/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if not isinstance(data, dict) or len(data) == 0:
            print("  Warning: zones.json returned an empty or invalid object.")
            return "{}"

        # Re-serialize as compact JSON object (no extra whitespace)
        result = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        print(f"  Loaded {len(data)} timezone entries from zones.json.")
        return result
    except Exception as e:
        print(f"  Warning: Could not fetch timezone data ({e}). Embedded TZ will be empty.")
        return "{}"


def _write_empty_header(output_file, symbol):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write("#pragma once\n")
        f.write(f"const uint8_t {symbol}[] = {{0}};\n")
        f.write(f"const size_t {symbol}_len = 0;\n")


def _write_gz_header(output_file, symbol, input_file, payload):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        f.write("#pragma once\n\n")
        f.write(f"// Generated from {input_file}\n")
        f.write(f"const uint8_t {symbol}[] = {{\n")
        hex_array = [f"0x{b:02x}" for b in payload]
        for i in range(0, len(hex_array), 16):
            f.write("    " + ", ".join(hex_array[i:i+16]) + ",\n")
        f.write("};\n\n")
        f.write(f"const size_t {symbol}_len = {len(payload)};\n")


def _process_html(input_file, output_file, symbol, inject_tz=False):
    if not os.path.exists(input_file):
        print(f"  Warning: {input_file} not found - emitting empty {symbol}.")
        _write_empty_header(output_file, symbol)
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        html_content = f.read()

    if inject_tz:
        tz_placeholder = "/*__TIMEZONE_DATA__*/{}"
        if tz_placeholder in html_content:
            tz_data = fetch_timezone_data()
            html_content = html_content.replace(tz_placeholder, tz_data)
            print(f"  [{symbol}] Timezone data injected ({len(tz_data)} chars).")

    raw_size = len(html_content.encode('utf-8'))
    html_content = minify_html(html_content)
    minified_bytes = html_content.encode('utf-8')
    compressed = gzip.compress(minified_bytes, compresslevel=9)

    print(f"  [{symbol}] {raw_size} -> minified {len(minified_bytes)} "
          f"-> gzip {len(compressed)} bytes "
          f"({100*len(compressed)//raw_size}% of raw)")

    _write_gz_header(output_file, symbol, input_file, compressed)


def generate_header(*args, **kwargs):
    print("Generating compressed HTML payload...")
    minifier = "minify-html" if _HAVE_MINIFY_HTML else "fallback (regex)"
    if not _HAVE_MINIFY_HTML:
        print("  Note: minify-html package not installed; using regex fallback.")
        print("        Run setup.sh for ~3 KB more savings per file.")
    else:
        print(f"  Minifier: {minifier}")

    _process_html(
        input_file="src/web/setup.html",
        output_file="include/setup_html_gz.h",
        symbol="setup_html_gz",
        inject_tz=True,
    )
    c_defines = env.ParseFlags(env.get("BUILD_FLAGS",[])).get("CPPDEFINES",[])

    if "ENABLE_OTA_UPDATES" in c_defines:
        _process_html(
            input_file="src/web/web_ui.html",
            output_file="include/web_ui_gz.h",
            symbol="web_ui_gz",
            inject_tz=False,
        )
    else:
        _process_html(
            input_file="src/web/web_ui_no_ota.html",
            output_file="include/web_ui_gz.h",
            symbol="web_ui_gz",
            inject_tz=False,
        )   

generate_header()
