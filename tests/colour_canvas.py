"""T1c: the colour canvas bytes, showing the 16 ANSI colours and 256-colour cells.

Copied from plans/reports/design-261015-tui-visual/tools/render_colour_canvas.py (design phase).
The state matches test_snapshots.py::test_t1 except the bytes the session in front has printed.
"""

E = "\x1b["
PROMPT = f"{E}1;32mops@infra-db-02{E}0m:{E}1;34m~{E}0m$ "


def swatch() -> str:
    """The 16 ANSI colours (0-7 normal, 8-15 bright), plus 256-colour cube and greys."""
    normal = "".join(f"{E}{40 + i}m{E}{30 if i else 37}m {i:>2} {E}0m" for i in range(8))
    bright = "".join(f"{E}{100 + i}m{E}30m {8 + i:>2} {E}0m" for i in range(8))
    cube = "".join(f"{E}48;5;{n}m  {E}0m" for n in (17, 25, 31, 37, 71, 107, 143, 179, 173, 167, 131, 96, 60))
    grey = "".join(f"{E}48;5;{n}m  {E}0m" for n in range(232, 256, 2))
    return (f"{normal}   normal 0-7\r\n{bright}   bright 8-15\r\n"
            f"{cube} {grey}   256-colour cube and greys (16-255)\r\n")


COLOUR = (
    f"{PROMPT}ls /etc/nginx\r\n"
    f"{E}1;34mconf.d{E}0m  mime.types  nginx.conf  {E}1;34msites-available{E}0m  {E}1;34msites-enabled{E}0m  "
    f"{E}1;36mmodules{E}0m\r\n"
    f"{PROMPT}ip -br -c addr\r\n"
    f"{E}36mlo              {E}0mUNKNOWN        {E}35m127.0.0.1{E}0m/8 {E}34m::1{E}0m/128\r\n"
    f"{E}36meth0            {E}0m{E}32mUP             {E}0m{E}35m192.0.2.10{E}0m/24 {E}34mfe80::5054:ff:fe12:3456{E}0m/64\r\n"
    f"{E}36meth1            {E}0m{E}31mDOWN           {E}0m\r\n"
    f"{PROMPT}systemctl status postgresql --no-pager | head -3\r\n"
    f"{E}0;1;32m●{E}0m postgresql.service - PostgreSQL RDBMS\r\n"
    f"     Loaded: loaded (/lib/systemd/system/postgresql.service; {E}0;1;32menabled{E}0m)\r\n"
    f"     Active: {E}0;1;32mactive (exited){E}0m since Thu 2026-10-15 08:12:04 +07; 1h 20min ago\r\n"
    f"{PROMPT}git -C /srv/app log --oneline -3\r\n"
    f"{E}33m3f9c2e1 {E}0m({E}1;36mHEAD -> {E}1;32mmain{E}0m, {E}1;31morigin/main{E}0m) fix: pool size from env\r\n"
    f"{E}33m8a17d40 {E}0mfeat: {E}1mreplica{E}0m health check\r\n"
    f"{E}33mc02be9f {E}0m{E}2mchore: bump deps{E}0m\r\n"
    f"{PROMPT}{E}31mERROR{E}0m disk /var/lib/postgresql {E}1;33m92%{E}0m full  {E}7m reverse video {E}0m\r\n"
    f"{swatch()}"
    f"{PROMPT}"
).encode()
