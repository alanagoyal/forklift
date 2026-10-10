#!/usr/bin/env python3
"""Render the code-only-revert demo: git undoes the code, Forklift undoes both.

Every terminal line is a condensed rendering of a real run recorded in
results/2026-10-09-code-only-revert/. The asserts below fail the render if the
evidence no longer says what the video shows.
"""
from pathlib import Path
import json, math, subprocess, sys
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent
E = ROOT.parent/'results/2026-10-09-code-only-revert'
summary = json.loads((E/'summary.json').read_text())
steps = {t['step']: t for t in json.loads((E/'transcript.json').read_text())}
assert summary['verified_code_schema_rows_restored']
assert summary['columns_before'].endswith(',paid')
assert summary['columns_after_agent'].endswith(',amount_paid_cents')
assert summary['columns_after_git_revert'] == summary['columns_after_agent']
assert summary['columns_after_restore'] == summary['columns_before']
assert 'DROP COLUMN paid' in summary['migration']
assert steps['agent']['exit'] == 0 and steps['git_revert']['exit'] == 0
assert steps['broken']['exit'] == 1
assert 'ERROR:  column "paid" does not exist' in steps['broken']['stderr']
assert 'non-zero exit status 1.' in steps['broken']['stderr']
for step in ['before', 'after_agent', 'restored']:
    assert json.loads(steps[step]['stdout'])['outstanding_cents'] == 17500
assert steps['restored']['stdout'] == steps['before']['stdout']

W = H = 1080
FPS = 30
C = {'bg': '#0a0a0a', 'panel': '#111110', 'line': '#272725', 'text': '#e9e7e2',
     'muted': '#b4b1aa', 'dim': '#6d6a64', 'accent': '#ff5b2e',
     'ok': '#3ecf8e', 'err': '#ff4d4d'}
MONO, SANS = str(ROOT/'fonts/GeistMonoVF.ttf'), str(ROOT/'fonts/GeistVF.ttf')


def font(path, size, weight='Regular'):
    f = ImageFont.truetype(path, size)
    f.set_variation_by_name(weight)
    return f


mono = font(MONO, 28)
mono_bold = font(MONO, 28, 'SemiBold')
symbol = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 28)
label = font(MONO, 17, 'Medium')
chip = font(MONO, 27, 'Medium')
narr1 = font(MONO, 30, 'SemiBold')
narr2 = font(MONO, 30, 'Regular')
SYMBOLS = set('✓✗✎')

# --- timeline -------------------------------------------------------------
A, B, CC, END, DURATION = 0, 9.8, 19.4, 30.5, 30.5
TYPE_CPS = 38


def cmd(at, text): return {'at': at, 'kind': 'cmd', 'text': text}
def out(at, text, kind='out'): return {'at': at, 'kind': kind, 'text': text}


scenes = [
    {'start': A, 'end': B, 'tag': ('agent', 'accent'),
     'narration': ('1. agent changes code + schema', '   migration drops invoices.paid', 'muted'),
     'lines': [cmd(.4, 'forklift project capture baseline'),
               out(1.7, '✓ saved code + database', 'ok'),
               cmd(2.5, 'codex exec "support partial payments"'),
               out(4.4, '  ✎ app.py', 'muted'),
               out(4.8, '  ✎ migrations/001_partial_payments.sql', 'muted'),
               out(5.3, '    ALTER TABLE invoices DROP COLUMN paid;', 'sql'),
               cmd(6.5, 'python3 app.py'),
               out(7.3, '✓ $175.00 outstanding · 3 invoices', 'ok')]},
    {'start': B, 'end': CC, 'tag': ('git', 'err'),
     'narration': ('2. revert code with git', '   old code, new schema: query fails', 'err'),
     'lines': [cmd(.4, 'git checkout -- app.py'),
               cmd(1.9, 'python3 app.py'),
               out(2.7, 'ERROR:  column "paid" does not exist', 'err'),
               out(2.85, 'LINE 1: ...customer, amount_cents, paid FROM ...', 'err'),
               out(2.85, '                                   ^', 'err'),
               out(3.15, 'Traceback (most recent call last):', 'dim'),
               out(3.25, '  File "app.py", line 8, in <module>', 'dim'),
               out(3.35, 'subprocess.CalledProcessError: Command', 'dim'),
               out(3.45, '  ... returned non-zero exit status 1.', 'dim')]},
    {'start': CC, 'end': END, 'tag': ('forklift', 'ok'),
     'narration': ('3. restore code + database with forklift', '   code, schema and rows from one snapshot', 'ok'),
     'lines': [cmd(.4, 'forklift project restore baseline'),
               out(2.0, '✓ app.py restored', 'ok'),
               out(2.25, '✓ database restored (schema + rows)', 'ok'),
               cmd(3.3, 'python3 app.py'),
               out(4.1, '✓ $175.00 outstanding · 3 invoices', 'ok')]},
]
for s in scenes:
    for l in s['lines']:
        assert len(l['text']) + 2 <= 54, l['text']
ERROR_AT = B + 2.7
# Card state changes, in absolute seconds.
CODE_V2_AT, DB_V2_AT = A + 4.4, A + 5.3
CODE_V1_AT = B + .4 + len('git checkout -- app.py')/TYPE_CPS + .2
DB_V1_AT = CC + 2.25


def ease(x): x = max(0, min(1, x)); return x*x*(3-2*x)


def mix(c1, c2, a):
    a = max(0, min(1, a))
    p = [int(c1[i:i+2], 16) for i in (1, 3, 5)]; q = [int(c2[i:i+2], 16) for i in (1, 3, 5)]
    return '#%02x%02x%02x' % tuple(round(p[i] + (q[i]-p[i])*a) for i in range(3))


def draw_text(d, x, y, s, color, f=mono):
    """Draw text, using Menlo for symbols Geist Mono lacks. Returns the end x."""
    for ch in s:
        g = symbol if ch in SYMBOLS else f
        d.text((x, y), ch, font=g, fill=color)
        x += f.getlength('M') if f in (mono, mono_bold) else g.getlength(ch)
    return x


def centered(d, y, s, f, color):
    d.text(((W - d.textlength(s, font=f))/2, y), s, font=f, fill=color)


# --- layout ---------------------------------------------------------------
PAD = 48
NARR_Y = 86
TERM = (PAD, 222, W-PAD, 754)
CARD_Y = (778, 918)
PILL_Y = 942


def terminal(d, t, scene):
    dt = t - scene['start']
    shake = 0
    if scene is scenes[1] and 0 <= t - ERROR_AT < .35:
        shake = math.sin((t-ERROR_AT)*90) * 9 * (1 - (t-ERROR_AT)/.35)
    x0, y0, x1, y1 = TERM; x0 += shake; x1 += shake
    broken = scene is scenes[1] and t >= ERROR_AT
    glow = ease(1 - (t-ERROR_AT)/.8) if broken else 0
    border = mix(C['line'], C['err'], .55 + .45*glow) if broken else C['line']
    d.rounded_rectangle((x0, y0, x1, y1), radius=16, fill=C['panel'], outline=border, width=2 + round(2*glow))
    for j in range(3):
        d.ellipse((x0+24+j*24, y0+22, x0+36+j*24, y0+34), fill=C['dim'])
    d.text((x0+112, y0+16), '~/invoices', font=label, fill=C['muted'])
    tag, tc = scene['tag']
    tw = d.textlength(tag.upper(), font=label)
    d.rounded_rectangle((x1-tw-48, y0+12, x1-20, y0+44), radius=8, outline=C[tc], width=2)
    d.text((x1-tw-34, y0+18), tag.upper(), font=label, fill=C[tc])
    d.line((x0, y0+56, x1, y0+56), fill=C['line'], width=2)
    y, cursor = y0+80, None
    for l in scene['lines']:
        if dt < l['at']:
            break
        if l['kind'] == 'cmd':
            n = int((dt - l['at']) * TYPE_CPS)
            shown = l['text'][:n]
            x = draw_text(d, x0+28, y, '$ ', C['accent'], mono_bold)
            x = draw_text(d, x, y, shown, C['text'])
            if n <= len(l['text']):
                cursor = (x, y)
        else:
            color = {'ok': C['ok'], 'err': C['err'], 'dim': C['dim'], 'muted': C['muted'],
                     'sql': C['accent']}.get(l['kind'], C['text'])
            draw_text(d, x0+28, y, l['text'], color)
            cursor = None
        y += 44
    if cursor is None:  # idle prompt once the scene's last output is shown
        if dt >= scene['lines'][-1]['at'] + .3:
            cursor = (draw_text(d, x0+28, y, '$ ', C['accent'], mono_bold), y)
            if int(t*2) % 2:
                cursor = None
    if cursor:
        d.rectangle((cursor[0]+2, cursor[1]+4, cursor[0]+16, cursor[1]+34), fill=C['text'])


def cards(d, t):
    code = 'amount_paid_cents' if CODE_V2_AT <= t < CODE_V1_AT else 'paid'
    db = 'amount_paid_cents' if DB_V2_AT <= t < DB_V1_AT else 'paid'
    sync = code == db
    half = (W - 2*PAD - 24) // 2
    for i, (title, verb, value, changed_at) in enumerate([
            ('CODE · app.py', 'reads', code, [CODE_V2_AT, CODE_V1_AT]),
            ('DATABASE · invoices', 'has', db, [DB_V2_AT, DB_V1_AT])]):
        x0 = PAD + i*(half+24); x1 = x0 + half
        flash = max([ease(1-(t-c)/.7) for c in changed_at if t >= c] + [0])
        fill = mix(C['panel'], '#2a1a12', flash)
        outline = C['line'] if sync else C['err']
        d.rounded_rectangle((x0, CARD_Y[0], x1, CARD_Y[1]), radius=14, fill=fill, outline=outline, width=2)
        d.text((x0+24, CARD_Y[0]+20), title, font=label, fill=C['muted'])
        d.text((x0+24, CARD_Y[0]+74), verb, font=chip, fill=C['dim'])
        cx = x0 + 24 + d.textlength(verb + ' ', font=chip)
        cw = d.textlength(value, font=chip)
        assert cx + cw + 20 < x1, value
        d.rounded_rectangle((cx-10, CARD_Y[0]+66, cx+cw+10, CARD_Y[0]+112), radius=8,
                            fill=mix(C['bg'], C['text'], .06))
        d.text((cx, CARD_Y[0]+74), value, font=chip, fill=C['text'])
    text = '✓  schema matches code' if sync else '✗  schema mismatch'
    color = C['ok'] if sync else C['err']
    w = d.textlength(text, font=chip) + 48
    x = (W - w)/2
    d.rounded_rectangle((x, PILL_Y, x+w, PILL_Y+52), radius=26, fill=mix(C['bg'], color, .14))
    draw_text(d, x+24, PILL_Y+10, text[0], color, chip)
    d.text((x+24+d.textlength(text[0], font=chip), PILL_Y+9), text[1:], font=chip, fill=color)


def narration(d, t, scene):
    a = ease((t - scene['start'])/.35)
    first, second, tone = scene['narration']
    for s, f in [(first, narr1), (second, narr2)]:
        assert d.textlength(s, font=f) < W - 2*PAD, s
    d.text((PAD, NARR_Y), first, font=narr1, fill=mix(C['bg'], C['text'], a))
    d.text((PAD, NARR_Y+48), second, font=narr2, fill=mix(C['bg'], C[tone], a))


def scene_frame(t):
    scene = next(s for s in scenes if s['start'] <= t < s['end'])
    im = Image.new('RGB', (W, H), C['bg']); d = ImageDraw.Draw(im)
    terminal(d, t, scene); cards(d, t); narration(d, t, scene)
    return im


def frame(t):
    return scene_frame(t)


if __name__ == '__main__':
    out = ROOT/'exports'
    for t in [2.0, 9.0, 13.5, 16.0, 25.0, 30.0]:
        frame(t).save(out/f'frame-{t:04.1f}.png')
    if '--frames' in sys.argv:
        sys.exit()
    frame(16.0).save(out/'poster.jpg', quality=92)
    mp4 = out/'forklift-code-only-revert.mp4'
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
                             '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-', '-an',
                             '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
                             '-movflags', '+faststart', str(mp4)], stdin=subprocess.PIPE)
    for i in range(int(FPS*DURATION)):
        proc.stdin.write(frame(i/FPS).tobytes())
    proc.stdin.close(); assert proc.wait() == 0
    (out/'metadata.json').write_text(json.dumps({
        'duration_seconds': DURATION, 'fps': FPS, 'width': W, 'height': H, 'audio': False,
        'presentation': 'animated replay of a real run; terminal output condensed; '
                        'python3 app.py ran via forklift project run to receive DATABASE_URL',
        'evidence': 'results/2026-10-09-code-only-revert'}, indent=2) + '\n')
    print(f'Wrote {mp4}')
