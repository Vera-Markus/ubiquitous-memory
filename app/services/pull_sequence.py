"""
The order characters are pulled in, and what happens when one fails: the safe-mode
sequence (ESI features plan 25.4, decisions D11.12, D11.13, D11.15).

1. First failure: the queue stops. A manual pull asks "Retry All?" (every character,
   including ones that succeeded); a background pull goes straight to step 2.
2. Second failure: a 5-minute safe mode, reading CCP's status page every 30 seconds.
3. After it: if the status page shows no error, the pull carries on from the character
   that failed. If it shows one, the pull is held (an outage, D11.8).
4. The same character fails again: it's dropped, and the pull carries on with the next.
5. The next one fails too: back to step 2.

Only server-side failures count (D11.13): errors in the 500s, timeouts, and ESI's rate
limits (420, 429). A login failure (401, 403) or anything else specific to one
character drops that character straight away, without safe mode.

Everything outside the sequence comes in as hooks, so the tests can drive every branch.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

SAFE_MODE_SECONDS = 300
PAGE_EVERY_SECONDS = 30

SERVER, CHARACTER = "server", "character"


@dataclass
class CharOutcome:
    """One character's pull: ok, or the HTTP status that ended it (None: no answer)."""
    ok: bool
    status: Optional[int] = None
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


def failure_kind(status: Optional[int]) -> str:
    """Server-side (counts towards safe mode) or specific to the character (dropped at once)."""
    if status is None or status >= 500 or status in (420, 429):
        return SERVER
    return CHARACTER


def character_message(outcome: CharOutcome) -> str:
    """Why a character was dropped without safe mode."""
    if outcome.status in (401, 403):
        return "log in again (Characters ▸ Add Character)"
    return outcome.message or f"ESI error {outcome.status}"


@dataclass
class SequenceResult:
    succeeded: List[str] = field(default_factory=list)
    dropped: Dict[str, str] = field(default_factory=dict)     # character -> why
    stopped: bool = False       # the user declined Retry All
    held: bool = False          # an outage: pulls wait until Tranquility is back

    @property
    def any_succeeded(self) -> bool:
        return bool(self.succeeded)


@dataclass
class Hooks:
    """What the sequence needs from outside. The defaults never wait and never ask."""
    manual: bool = False
    safe_mode: bool = True
    ask_retry: Callable[[str, CharOutcome], Awaitable[bool]] = None     # manual: "Retry All?"
    esi_up: Callable[[], bool] = lambda: True                          # D11.15
    page_has_error: Callable[[], Optional[bool]] = lambda: None        # None: couldn't read it
    notify: Callable[[str], None] = lambda text: None                  # the status line
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    name: Callable[[str], str] = lambda char: char


class PullSequence:
    def __init__(self, pull_one: Callable[[str], Awaitable[CharOutcome]], hooks: Optional[Hooks] = None):
        self.pull_one = pull_one
        self.hooks = hooks or Hooks(safe_mode=False)

    async def run(self, chars: List[str]) -> SequenceResult:
        h = self.hooks
        queue = list(chars)
        result = SequenceResult()
        done: List[str] = []
        retried = False             # the manual Retry All has been used
        after_safe_mode = None      # the character safe mode was entered for
        i = 0
        while i < len(queue):
            char = queue[i]
            h.notify(f"Pulling {h.name(char)} ({i + 1} of {len(queue)})…")
            outcome = await self.pull_one(char)
            if outcome.ok:
                done.append(char)
                after_safe_mode = None if after_safe_mode == char else after_safe_mode
                i += 1
                continue
            if failure_kind(outcome.status) == CHARACTER:
                result.dropped[char] = character_message(outcome)
                h.notify(f"{h.name(char)} skipped: {result.dropped[char]}")
                i += 1
                continue
            # A server-side failure.
            if not h.safe_mode:
                result.dropped[char] = outcome.message or f"ESI error {outcome.status}"
                i += 1
                continue
            if after_safe_mode == char:
                result.dropped[char] = f"ESI error {outcome.status}" if outcome.status else "ESI didn't answer"
                h.notify(f"{h.name(char)} skipped: {result.dropped[char]}")
                after_safe_mode = None
                i += 1
                continue
            if h.manual and not retried:
                retried = True
                if not h.ask_retry or not await h.ask_retry(char, outcome):
                    result.stopped = True
                    break
                done.clear()            # Retry All: every character again
                i = 0
                continue
            if not h.esi_up():
                result.held = True      # an outage, not a hiccup: wait for Tranquility (D11.8, D11.15)
                break
            if not await self._safe_mode():
                result.held = True
                break
            after_safe_mode = char      # carry on from the character that failed
        result.succeeded = done
        return result

    async def _safe_mode(self) -> bool:
        """5 minutes, reading the status page every 30 s. False if it shows an error (an outage)."""
        h = self.hooks
        for second in range(SAFE_MODE_SECONDS):
            if second % PAGE_EVERY_SECONDS == 0 and h.page_has_error():
                h.notify("CCP's status page reports a problem: pulls are on hold until Tranquility is back.")
                return False
            left = SAFE_MODE_SECONDS - second
            h.notify(f"Safe mode: checking CCP's status page ({left // 60}:{left % 60:02d} left)")
            await h.sleep(1)
        h.notify("Safe mode over: trying again.")
        return True
