## Chinese (zh_CN) Localisation

### Font requirement

The OpenFollow Pi image (Raspberry Pi OS Lite) does **not** include
CJK (Chinese / Japanese / Korean) fonts by default.  On the embedded
browser (Cairo / cage), Chinese text renders as □ until a font is
installed:

```bash
# Install Noto Sans CJK (recommended, ~15 MB):
sudo apt install fonts-noto-cjk

# Or drop any .ttf covering the required codepoints into:
# /usr/share/fonts/truetype/
sudo fc-cache -fv
sudo systemctl restart openfollow
```

The web UI accessed from a desktop browser (Chrome / Edge / Firefox)
is **not** affected — those browsers ship their own CJK fonts.
