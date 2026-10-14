"""`tsb-tui drive`: the TUI made a user for an agent, and the harness's scenario language (design §2.9).

drive runs the real app headless on Textual's own test runner. It reads one JSON command per line and writes
answers and events, one JSON object per line. It adds no API: every command goes through the same action a
click or a key fires, every read is made in this client's own grid, and every judgement about output is made
here from bytes already received (Contract §6, §11, §13).

  * `commands.py` the commands and their answers;
  * `waits.py`    the predicates and when a wait fails;
  * `events.py`   what the models say, as the events the agent reads;
  * `runner.py`   the process: the app, the lines in and out, the exit code;
  * `GUIDE.md`    what an agent is told (`tsb-tui drive --guide` prints it).
"""
