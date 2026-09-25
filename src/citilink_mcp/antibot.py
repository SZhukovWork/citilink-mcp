"""Solver for Citilink's own "_pcl" JavaScript challenge.

Citilink sometimes answers with HTTP 429 and a small HTML page titled
"Загрузка..." instead of data (seen on /graphql/ too once an IP has made a few
dozen requests without the cookie). The page's script:

- assembles a string from chunks, some of them reversed;
- allocates a 512 MiB ``Int8Array`` filled with one repeated 1 KiB block and
  bumps a few offsets in every block;
- counts the bytes equal to a target value and trims the string by the last
  non-zero digit of that count;
- stores the result in the cookie named by ``data-name`` (``_pcl``) on
  ``data-domain`` for one hour and reloads the page.

Every block is identical, so the count is ``count_in_one_block * blocks`` and
no 512 MiB allocation is needed. The result was cross-checked against running
the original script in node (identical value) and accepted by the live site.

``solve`` returns ``None`` when the page is not this challenge or its layout
changed — the caller must then fail loudly, never guess.
"""
from __future__ import annotations

import re
from typing import NamedTuple


class Solution(NamedTuple):
    name: str
    value: str
    domain: str


def is_challenge(html: str) -> bool:
    """Whether an HTML body looks like the "_pcl" challenge page at all."""
    return "Загрузка..." in html and "data-name=" in html


def solve(html: str) -> Solution | None:
    m = re.search(r'data-name="([^"]+)"\s+data-domain="([^"]+)"', html)
    if not m or "new Int8Array(" not in html:
        return None
    name, domain = m.group(1), m.group(2)
    try:
        script = html[html.index("<script>", html.index("js.cookie")):]
        value = ""
        for chunk, rev in re.findall(
            r"var value2 = '([^']*)';\s*(value2 = value2\.split\(\"\"\)\.reverse\(\)\.join\(\"\"\))?", script
        ):
            value += chunk[::-1] if rev else chunk
        phrase_src = re.search(r"var phrase = new Int8Array\(\[([^\]]*)\]\)", script)
        total_src = re.search(r"var data = new Int8Array\((\d+)\)", script)
        step_src = re.search(r"i < data\.length; i\+=(\d+)\) \{\s*data\.set\(phrase, i\)", script)
        target_src = re.search(r"if \(data\[i\] === (-?\d+)\)", script)
        if not (phrase_src and total_src and step_src and target_src and value):
            return None
        phrase = [int(x) for x in phrase_src.group(1).split(",") if x.strip()]
        total, step = int(total_src.group(1)), int(step_src.group(1))
        if len(phrase) > step or total % step:
            return None
        block = phrase + [0] * (step - len(phrase))
        for off, st, k1, k2, k3 in re.findall(
            r"for\(var i = (\d+); i < data\.length; i\+=(\d+)\) \{\s*data\[i\] = data\[i\] \+ (\d+) > 127 \? "
            r"data\[i\] -128 \+ (\d+) : data\[i\] \+ (\d+);", script
        ):
            if int(st) != step or not (k1 == k2 == k3) or int(off) >= step:
                return None
            off_i, k = int(off), int(k1)
            v = block[off_i]
            v = v - 128 + k if v + k > 127 else v + k
            block[off_i] = ((v + 128) % 256) - 128  # Int8Array store semantics
        count = block.count(int(target_src.group(1))) * (total // step)
        trim = 5
        for ch in reversed(str(count)):
            if int(ch) > 0:
                trim = int(ch)
                break
        solved = value[: len(value) - trim]
        return Solution(name, solved, domain) if solved else None
    except (AttributeError, ValueError, IndexError):
        return None
