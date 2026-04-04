from re import compile
from urllib.parse import urlparse

from pyperclip import paste
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, HorizontalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, TextArea

from ..application import XHS
from ..translation import _

__all__ = ["Record"]


class Record(ModalScreen):
    ID = compile(r"^[0-9A-Za-z]{16,32}$")
    LINK = "https://www.xiaohongshu.com/explore/{0}"
    BINDINGS = [
        Binding("ctrl+v", "paste_clipboard", show=False),
    ]

    def __init__(
        self,
        app: XHS,
    ):
        super().__init__()
        self.xhs = app

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(
                _(
                    "\u8bf7\u76f4\u63a5\u7c98\u8d34\u5c0f\u7ea2\u4e66\u5206\u4eab\u6587\u672c\u3001\u4f5c\u54c1\u94fe\u63a5\u6216\u4f5c\u54c1 ID"
                ),
                classes="prompt",
            ),
            TextArea(
                "",
                placeholder=_(
                    "\u4f8b\u5982\uff1a\u6574\u6bb5\u5206\u4eab\u6587\u6848\uff0c\u6216 https://www.xiaohongshu.com/discovery/item/... \uff0c\u4e5f\u53ef\u4ee5\u53ea\u7c98\u8d34 ID"
                ),
                soft_wrap=True,
                show_line_numbers=False,
                id="payload",
            ),
            Label(
                _(
                    "\u7a0b\u5e8f\u6570\u636e\u5e93\u91cc\u5b58\u7684\u662f\u4f5c\u54c1 ID\uff0c\u4f8b\u5982\uff1a69ce55ac0000000022000eb0\uff1b\u7c98\u8d34\u6574\u6bb5\u5206\u4eab\u6587\u672c\u540e\u4f1a\u81ea\u52a8\u63d0\u53d6"
                ),
                id="record_hint",
            ),
            HorizontalScroll(
                Button(
                    _("\u8bfb\u53d6\u526a\u8d34\u677f"),
                    id="paste",
                ),
                Button(
                    _("\u5220\u9664\u6307\u5b9a\u4f5c\u54c1 ID"),
                    id="enter",
                ),
                Button(_("\u8fd4\u56de\u9996\u9875"), id="close"),
            ),
            id="record",
        )

    def on_show(self) -> None:
        self.query_one(TextArea).focus()

    def set_payload(self, value: str) -> None:
        text = self.query_one(TextArea)
        text.load_text(value)
        text.cursor_location = (0, 0)
        text.focus()

    @classmethod
    def extract_token_id(cls, token: str) -> str:
        token = token.strip()
        if not token:
            return ""
        if "xiaohongshu.com" in token:
            if "://" not in token:
                token = f"https://{token}"
            path = urlparse(token).path.rstrip("/")
            return path.split("/")[-1] if path else ""
        return token if cls.ID.fullmatch(token) else ""

    def update_hint(self, text: str) -> None:
        ids = list(
            dict.fromkeys(
                i for i in (self.extract_token_id(j) for j in text.split()) if i
            )
        )
        hint = self.query_one("#record_hint", Label)
        if not text.strip():
            hint.update(
                _(
                    "\u7a0b\u5e8f\u6570\u636e\u5e93\u91cc\u5b58\u7684\u662f\u4f5c\u54c1 ID\uff0c\u4f8b\u5982\uff1a69ce55ac0000000022000eb0\uff1b\u7c98\u8d34\u6574\u6bb5\u5206\u4eab\u6587\u672c\u540e\u4f1a\u81ea\u52a8\u63d0\u53d6"
                )
            )
        elif not ids:
            hint.update(
                _(
                    "\u6682\u672a\u4ece\u5f53\u524d\u5185\u5bb9\u91cc\u8bc6\u522b\u5230\u4f5c\u54c1 ID\uff1b\u4f46\u5982\u679c\u4f60\u7c98\u8d34\u7684\u662f\u77ed\u94fe\u6216\u5b8c\u6574\u5206\u4eab\u6587\u672c\uff0c\u70b9\u201c\u5220\u9664\u201d\u65f6\u4ecd\u4f1a\u7ee7\u7eed\u5c1d\u8bd5\u63d0\u53d6"
                )
            )
        elif len(ids) == 1:
            hint.update(_("\u5df2\u8bc6\u522b\u5230\u4f5c\u54c1 ID\uff1a{0}").format(ids[0]))
        else:
            hint.update(
                _("\u5df2\u8bc6\u522b\u5230 {0} \u4e2a\u4f5c\u54c1 ID\uff1a{1}").format(
                    len(ids),
                    ", ".join(ids[:3]),
                )
            )

    async def extract_ids(self, text: str) -> list[str]:
        ids = [self.extract_token_id(i) for i in text.split()]
        ids.extend(
            self.extract_token_id(i)
            for i in await self.xhs.extract_links(
                text,
            )
        )
        ids = [i for i in ids if i]
        return list(dict.fromkeys(ids))

    async def delete(self, text: str) -> bool:
        ids = await self.extract_ids(text)
        if not ids:
            self.app.notify(_("\u672a\u8bc6\u522b\u5230\u53ef\u5220\u9664\u7684\u4f5c\u54c1 ID"))
            return False
        await self.xhs.id_recorder.delete(ids)
        self.app.notify(
            _("\u5df2\u5220\u9664 {0} \u6761\u4e0b\u8f7d\u8bb0\u5f55").format(len(ids))
        )
        return True

    def action_paste_clipboard(self) -> None:
        text = paste()
        if not text:
            self.app.notify(_("\u526a\u8d34\u677f\u6ca1\u6709\u53ef\u7528\u5185\u5bb9"))
            return
        self.set_payload(text)
        self.update_hint(text)

    @on(TextArea.Changed, "#payload")
    def preview_payload(self, event: TextArea.Changed) -> None:
        self.update_hint(event.text_area.text)

    @on(Button.Pressed, "#paste")
    def paste_button(self) -> None:
        self.action_paste_clipboard()

    @on(Button.Pressed, "#enter")
    async def save_settings(self):
        text = self.query_one(TextArea)
        if await self.delete(text.text):
            text.load_text("")
            self.update_hint("")
            text.focus()

    @on(Button.Pressed, "#close")
    def reset(self):
        self.dismiss()
