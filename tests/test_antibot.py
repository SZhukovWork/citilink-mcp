"""The "_pcl" challenge solver on a recorded (redacted) challenge page."""
import json
import re
import shutil
import subprocess

import pytest

from citilink_mcp import antibot
from conftest import load

HTML = load("challenge_429_redacted.html")


def test_solves_the_recorded_challenge():
    sol = antibot.solve(HTML)
    assert sol is not None
    assert (sol.name, sol.domain) == ("_pcl", ".citilink.ru") and sol.value
    assert antibot.is_challenge(HTML)


def test_not_a_challenge():
    assert antibot.solve("<html><title>Ситилинк</title></html>") is None
    assert not antibot.is_challenge('{"data":{}}')


def test_changed_layout_is_refused_not_guessed():
    broken = HTML.replace("var phrase = new Int8Array", "var phrase2 = new Uint8Array")
    assert antibot.solve(broken) is None
    moved = re.sub(r"i\+=(\d+)\) \{\s*data\.set", "i+=7) { data.set", HTML, count=1)
    assert antibot.solve(moved) is None


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_matches_the_original_script_in_node():
    js = re.findall(r"<script>(.*?)</script>", HTML, re.S)[-1]
    harness = (
        "let out={};const document={addEventListener:(e,f)=>{globalThis.__f=f},"
        "getElementById:()=>({dataset:{name:'_pcl',domain:'.citilink.ru'}}),location:{reload(){}}};"
        "const Cookies={set:(n,v,o)=>{out={n,v}}};console.info=()=>{};" + js
        + ";__f();process.stdout.write(JSON.stringify(out))"
    )
    r = subprocess.run([shutil.which("node"), "--max-old-space-size=2048", "-e", harness],
                       capture_output=True, text=True, timeout=120)
    assert json.loads(r.stdout)["v"] == antibot.solve(HTML).value
