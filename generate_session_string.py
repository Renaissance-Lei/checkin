import asyncio
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


def read_required_env(name: str) -> str:
    """
    读取必填环境变量。

    这个脚本是给你本地手动运行一次用的，运行完成后会输出一条 StringSession，
    你把它复制到 GitHub Secret `TG_SESSION_STRING` 里即可。
    """

    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"环境变量 {name} 未设置。")
    return value


async def main() -> None:
    """
    生成 Telethon 的字符串会话。

    使用方式：
    1. 本地设置 TG_API_ID / TG_API_HASH / TG_PHONE_NUMBER
    2. 运行本文件
    3. 按提示输入验证码和二步密码
    4. 复制打印出的字符串到 GitHub Secrets
    """

    api_id = int(read_required_env("TG_API_ID"))
    api_hash = read_required_env("TG_API_HASH")
    phone_number = read_required_env("TG_PHONE_NUMBER")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            await client.send_code_request(phone_number)
            code = input("请输入 Telegram 验证码: ").strip()

            try:
                await client.sign_in(phone=phone_number, code=code)
            except SessionPasswordNeededError:
                password = input("检测到两步验证，请输入 Telegram 二步密码: ").strip()
                await client.sign_in(password=password)

        session_string = client.session.save()
        print("\n以下就是要填入 GitHub Secret `TG_SESSION_STRING` 的值：\n")
        print(session_string)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
