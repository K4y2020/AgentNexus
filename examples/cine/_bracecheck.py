from pathlib import Path

src = Path('skills/cine-script/scripts/cine-script.mjs').read_text(encoding='utf-8')
i, n, line, depth = 0, len(src), 1, 0
stack = []
BACKSLASH = chr(92)

while i < n:
    c = src[i]
    if c == '\n':
        line += 1
        i += 1
        continue
    if c == '/' and i + 1 < n and src[i + 1] == '/':
        while i < n and src[i] != '\n':
            i += 1
        continue
    if c == '/' and i + 1 < n and src[i + 1] == '*':
        i += 2
        while i + 1 < n and not (src[i] == '*' and src[i + 1] == '/'):
            if src[i] == '\n':
                line += 1
            i += 1
        i += 2
        continue
    if c in ('"', "'", '`'):
        q = c
        i += 1
        while i < n:
            if src[i] == BACKSLASH:
                i += 2
                continue
            if src[i] == '\n':
                line += 1
            if src[i] == q:
                break
            i += 1
        i += 1
        continue
    if c == '{':
        depth += 1
        stack.append(line)
    elif c == '}':
        depth -= 1
        if stack:
            stack.pop()
    i += 1

print("final depth:", depth)
print("unclosed braces opened at lines:", stack[:12])
