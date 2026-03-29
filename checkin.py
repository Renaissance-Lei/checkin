import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message


def read_env_str(name: str, default: str) -> str:
    """
    读取字符串环境变量。

    设计原因：
    1. 本地运行时你仍然可以直接改默认值快速测试
    2. GitHub Actions 里则优先读取 Secrets/Variables，不把敏感信息写进仓库
    """

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def read_env_int(name: str, default: int) -> int:
    """
    读取整数环境变量。

    如果环境变量格式错误，会尽早抛异常，避免后面连 Telegram 时才发现问题。
    """

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def read_env_bool(name: str, default: bool) -> bool:
    """
    读取布尔环境变量。

    支持的真值：
    - true
    - 1
    - yes
    - on

    改成 false/0/no/off 则会关闭对应功能。
    """

    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_env_list(name: str, default: list[str]) -> list[str]:
    """
    读取逗号分隔的字符串列表环境变量。

    例如：
    TG_SUCCESS_KEYWORDS="签到成功,领取成功,今日已领"

    改动这个参数会直接影响“成功/已签到”的文案识别范围。
    """

    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    return [item.strip() for item in value.split(",") if item.strip()]


# =========================
# 这里是最重要的配置区
# =========================
# 当前脚本已经改成“环境变量优先，文件默认值兜底”的模式。
#
# 推荐做法：
# 1. GitHub Actions：把敏感信息放到 GitHub Secrets，不要写死在代码里
# 2. 本地手动运行：可以临时改下面默认值，或者在命令行里设置同名环境变量

# Telegram API 凭证：
# 优先读取：
# - TG_API_ID
# - TG_API_HASH
#
# 这两个参数决定能否成功连接 Telegram。
# - API_ID / API_HASH 配错：脚本无法登录
# - API_ID 不是整数：启动时会直接报错
API_ID = read_env_int("TG_API_ID", 12345678)
API_HASH = read_env_str("TG_API_HASH", "replace_with_your_api_hash")

# 登录手机号：
# 优先读取 TG_PHONE_NUMBER
#
# 这个参数只在“需要重新登录”时才会用到。
# 如果你已经准备好了 TG_SESSION_STRING，那么理论上 GitHub Actions 运行时可以不依赖它，
# 但我仍然建议保留，方便你以后重新生成会话。
PHONE_NUMBER = read_env_str("TG_PHONE_NUMBER", "+8613800000000")

# 目标机器人用户名：
# 优先读取 TG_BOT_USERNAME
BOT_USERNAME = read_env_str("TG_BOT_USERNAME", "@replace_with_your_bot")

# 目标按钮文字：
# 优先读取 TG_TARGET_BUTTON_TEXT
# 如果机器人按钮文案变了，这里必须同步修改。
TARGET_BUTTON_TEXT = read_env_str("TG_TARGET_BUTTON_TEXT", "获取流量")

# 目标按钮位置：
# 优先读取：
# - TG_TARGET_BUTTON_ROW
# - TG_TARGET_BUTTON_COL
#
# 用户描述是“第五行第二个”，这里仍然保留 1 基下标，便于直观理解。
# - 行号调大：会尝试点击更靠下的一行
# - 列号调大：会尝试点击该行更靠右的按钮
TARGET_BUTTON_ROW = read_env_int("TG_TARGET_BUTTON_ROW", 5)
TARGET_BUTTON_COL = read_env_int("TG_TARGET_BUTTON_COL", 2)

# 每天执行时间，24 小时制。
# 优先读取 TG_DAILY_RUN_TIME
#
# 这个参数主要给“本地常驻运行模式”使用。
# GitHub Actions 推荐直接用 cron 控制定时，脚本本身使用 --once 执行一次即可。
DAILY_RUN_TIME = read_env_str("TG_DAILY_RUN_TIME", "09:00")

# 时区：
# 优先读取 TG_TIMEZONE
# - 改成 Asia/Shanghai：按北京时间判断“今天”
# - 改成 UTC：按 UTC 日期判断“今天”
TIMEZONE = read_env_str("TG_TIMEZONE", "Asia/Shanghai")

# 是否在正式点击前，先检查今天是不是已经签到/领取过。
# 优先读取 TG_CHECK_BEFORE_SIGN
# - True：更稳，能减少重复点击
# - False：每次都会直接尝试签到
CHECK_BEFORE_SIGN = read_env_bool("TG_CHECK_BEFORE_SIGN", True)

# 是否启用本地状态文件做二次保护。
# 优先读取 TG_USE_LOCAL_STATE_GUARD
# - True：适合本地常驻运行
# - False：适合 GitHub Actions 这种无状态运行环境
USE_LOCAL_STATE_GUARD = read_env_bool("TG_USE_LOCAL_STATE_GUARD", True)

# 成功关键词：
# 优先读取 TG_SUCCESS_KEYWORDS，格式为逗号分隔。
SUCCESS_KEYWORDS = read_env_list(
    "TG_SUCCESS_KEYWORDS",
    [
        "签到成功",
        "领取成功",
        "获取成功",
        "已获得",
        "已领取",
        "获得流量",
    ],
)

# 已签到/已领取关键词：
# 优先读取 TG_ALREADY_DONE_KEYWORDS，格式为逗号分隔。
ALREADY_DONE_KEYWORDS = read_env_list(
    "TG_ALREADY_DONE_KEYWORDS",
    [
        "已签到",
        "已经签到",
        "今日已签到",
        "今日已领取",
        "今日已获取",
        "今天已经",
    ],
)

# 轮询等待参数：
# 优先读取：
# - TG_START_RESPONSE_TIMEOUT
# - TG_CLICK_RESULT_TIMEOUT
# - TG_POLL_INTERVAL_SECONDS
#
# 调大等待超时：更能容忍机器人回复慢，但一次运行会更久
# 调小轮询间隔：响应更快，但请求频率更高
START_RESPONSE_TIMEOUT = read_env_int("TG_START_RESPONSE_TIMEOUT", 20)
CLICK_RESULT_TIMEOUT = read_env_int("TG_CLICK_RESULT_TIMEOUT", 20)
POLL_INTERVAL_SECONDS = read_env_int("TG_POLL_INTERVAL_SECONDS", 2)

# 最近消息扫描条数：
# 优先读取 TG_RECENT_MESSAGE_LIMIT
# 调大：更容易从聊天记录中找到目标消息
# 调小：查询更快，但可能漏掉旧一点的菜单消息
RECENT_MESSAGE_LIMIT = read_env_int("TG_RECENT_MESSAGE_LIMIT", 15)

# 会话配置：
# - TG_SESSION_STRING：给 GitHub Actions 用的字符串会话
# - TG_SESSION_NAME：给本地文件会话用的文件名前缀
#
# 使用建议：
# 1. GitHub Actions：优先使用 TG_SESSION_STRING
# 2. 本地调试：继续使用 TG_SESSION_NAME 生成 .session 文件也没问题
SESSION_STRING = read_env_str("TG_SESSION_STRING", "")
SESSION_NAME = read_env_str("TG_SESSION_NAME", "telegram_checkin")

# 本地状态文件路径：
# 优先读取 TG_STATE_FILE
# GitHub Actions 中通常不需要持久化这个文件，所以工作流里建议把本地状态保护关闭。
STATE_FILE = read_env_str("TG_STATE_FILE", "checkin_state.json")

# 日志级别：
# 优先读取 TG_LOG_LEVEL
# - INFO：常规运行日志
# - DEBUG：更详细，排查问题更方便
LOG_LEVEL_NAME = read_env_str("TG_LOG_LEVEL", "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)


@dataclass
class CheckinResult:
    """统一封装一次签到流程的结果，方便后续记录状态和打印日志。"""

    status: str
    detail: str


def setup_logging() -> None:
    """初始化日志格式，方便你后续观察脚本运行到哪一步。"""

    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def normalize_bot_username(value: str) -> str:
    """确保机器人用户名带上 @，减少配置时的小错误。"""

    value = value.strip()
    if not value.startswith("@"):
        value = f"@{value}"
    return value


def validate_config() -> None:
    """
    在真正连接 Telegram 前，先做一次配置检查。

    这里做成“尽早失败”，这样一旦 GitHub Secrets 漏填，工作流日志会第一时间告诉你。
    """

    if not isinstance(API_ID, int):
        raise ValueError("API_ID 必须是整数，请检查 TG_API_ID。")

    if not API_HASH or API_HASH == "replace_with_your_api_hash":
        raise ValueError("API_HASH 未配置，请填写 TG_API_HASH。")

    if not SESSION_STRING and (not PHONE_NUMBER or PHONE_NUMBER == "+8613800000000"):
        raise ValueError("未提供可用会话，且 PHONE_NUMBER 仍是占位值，请填写 TG_PHONE_NUMBER。")

    if not BOT_USERNAME or "replace_with_your_bot" in BOT_USERNAME:
        raise ValueError("BOT_USERNAME 未配置，请填写 TG_BOT_USERNAME。")

    if TARGET_BUTTON_ROW <= 0 or TARGET_BUTTON_COL <= 0:
        raise ValueError("TARGET_BUTTON_ROW 和 TARGET_BUTTON_COL 必须从 1 开始。")

    try:
        datetime.strptime(DAILY_RUN_TIME, "%H:%M")
    except ValueError as exc:
        raise ValueError('TG_DAILY_RUN_TIME 必须是 "HH:MM" 格式，例如 "09:00"。') from exc


def load_state() -> dict:
    """
    读取本地状态文件。
    这个文件只作为“防止重复尝试”的辅助信息，不是核心逻辑。
    """

    path = Path(STATE_FILE)
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logging.warning("状态文件损坏，将忽略旧状态并重新生成。")
        return {}


def save_state(state: dict) -> None:
    """把当天执行结果写回本地，方便脚本重启后继续避开重复签到。"""

    Path(STATE_FILE).write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_timezone() -> ZoneInfo:
    """统一获取时区对象，避免在多个函数里重复创建。"""

    return ZoneInfo(TIMEZONE)


def now_local() -> datetime:
    """返回当前本地时区时间。"""

    return datetime.now(get_timezone())


def start_of_today() -> datetime:
    """返回今天 00:00:00 的本地时区时间，用来判断“是不是今天的消息”。"""

    current = now_local()
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def contains_keyword(text: str, keywords: Iterable[str]) -> bool:
    """
    判断文本里是否包含任一关键词。
    这里使用“包含”而不是“完全相等”，因为很多机器人会在长句中包含提示词。
    """

    if not text:
        return False

    return any(keyword in text for keyword in keywords)


def summarize_message(message: Optional[Message]) -> str:
    """
    把 Telegram 消息压缩成短文本，方便日志输出。
    有些按钮消息正文可能为空，所以这里要做兜底。
    """

    if message is None:
        return "无消息"

    text = (message.raw_text or "").strip()
    if text:
        return text.replace("\n", " ")[:120]

    if message.buttons:
        return "消息包含按钮，但正文为空"

    return "空消息"


def can_prompt_for_login() -> bool:
    """
    判断当前环境是否适合交互式输入验证码。

    设计原因：
    GitHub Actions 没有人手动输入验证码，所以一旦没有预先准备好的 StringSession，
    就应该直接报错，而不是卡在 input()。
    """

    return sys.stdin.isatty()


def build_client() -> TelegramClient:
    """
    根据当前配置选择会话类型。

    规则：
    1. 有 TG_SESSION_STRING 时，优先使用字符串会话，适合 GitHub Actions
    2. 否则退回本地文件会话，适合本地长期运行
    """

    if SESSION_STRING:
        return TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    return TelegramClient(SESSION_NAME, API_ID, API_HASH)


async def ensure_login(client: TelegramClient) -> None:
    """
    确保客户端已经登录。

    登录规则：
    1. 已授权：直接复用
    2. 有 TG_SESSION_STRING 但失效：直接报错，让你重新生成会话
    3. 本地交互环境：允许输入验证码完成登录
    4. 非交互环境且没有有效会话：直接报错
    """

    await client.connect()

    if await client.is_user_authorized():
        logging.info("Telegram 已登录，无需重复授权。")
        return

    if SESSION_STRING:
        raise RuntimeError(
            "TG_SESSION_STRING 当前不可用或已失效，请在本地重新生成新的会话字符串后更新 GitHub Secret。"
        )

    if not can_prompt_for_login():
        raise RuntimeError(
            "当前环境不支持交互式登录，请先在本地生成 TG_SESSION_STRING，再用于 GitHub Actions。"
        )

    logging.info("当前会话未登录，开始请求验证码。")
    await client.send_code_request(PHONE_NUMBER)
    code = input("请输入 Telegram 验证码: ").strip()

    try:
        await client.sign_in(phone=PHONE_NUMBER, code=code)
    except SessionPasswordNeededError:
        password = input("检测到两步验证，请输入 Telegram 二步密码: ").strip()
        await client.sign_in(password=password)

    logging.info("登录成功，会话已保存到本地。")


async def get_recent_bot_messages(
    client: TelegramClient,
    bot_username: str,
    limit: int = RECENT_MESSAGE_LIMIT,
) -> list[Message]:
    """
    读取机器人最近消息。
    这里只保留“机器人发来的消息”，不把自己发出去的 /start 或按钮文本混进来。
    """

    messages = await client.get_messages(bot_username, limit=limit)
    return [message for message in messages if not message.out]


async def detect_today_status(
    client: TelegramClient,
    bot_username: str,
) -> Optional[CheckinResult]:
    """
    检查今天是否已经签到。

    判断优先级：
    1. 如果发现“已签到/今日已领取”类关键词，直接认为今天做过了
    2. 如果发现“签到成功/领取成功”类关键词，也认为今天已经完成
    3. 如果今天没有相关消息，则返回 None，表示可以继续尝试
    """

    today_start = start_of_today()
    recent_messages = await get_recent_bot_messages(client, bot_username)

    for message in recent_messages:
        message_time = message.date.astimezone(get_timezone())
        if message_time < today_start:
            continue

        text = message.raw_text or ""
        if contains_keyword(text, ALREADY_DONE_KEYWORDS):
            return CheckinResult("already_done", f"今天已检测到已签到提示: {text[:100]}")

        if contains_keyword(text, SUCCESS_KEYWORDS):
            return CheckinResult("success", f"今天已检测到成功提示: {text[:100]}")

    return None


def get_button_text(message: Message, row_index: int, col_index: int) -> Optional[str]:
    """
    获取指定位置按钮的文字。
    行列使用 0 基坐标，因为 Telethon 的 click(row, col) 也是这样要求的。
    """

    if not message.buttons:
        return None

    if row_index >= len(message.buttons):
        return None

    row = message.buttons[row_index]
    if col_index >= len(row):
        return None

    button = row[col_index]
    return getattr(button, "text", None)


def find_button_message(messages: list[Message]) -> Optional[Message]:
    """
    改进版：
    不再依赖固定行列，改为“关键词模糊匹配按钮”
    """

    for message in messages:
        if not message.buttons:
            continue

        for row in message.buttons:
            for button in row:
                text = (getattr(button, "text", "") or "").strip()

                # 👇 调试日志（非常关键）
                logging.info("DEBUG按钮: %r", text)

                # 👇 核心：模糊匹配
                if TARGET_BUTTON_TEXT in text:
                    return message

    return None


async def wait_for_button_message(
    client: TelegramClient,
    bot_username: str,
    timeout_seconds: int,
    previous_message_ids: Optional[set[int]] = None,
) -> Optional[Message]:
    """
    发完 /start 后等待机器人返回带目标按钮的消息。
    使用轮询而不是只等单条响应，是为了兼容“机器人连续发多条消息”的场景。
    

async def click_target_button(message: Message) -> None:
    """
    改进版：
    直接遍历按钮，用“包含匹配”点击，而不是固定位置
    """

    for row in message.buttons:
        for button in row:
            text = (getattr(button, "text", "") or "").strip()

            if TARGET_BUTTON_TEXT in text:
                await button.click()
                logging.info("已点击按钮: %s", text)
                return

    raise RuntimeError("找到了按钮消息，但未找到可点击的目标按钮")

async def wait_for_result_after_click(
    client: TelegramClient,
    bot_username: str,
    timeout_seconds: int,
) -> CheckinResult:
    """
    点击按钮后等待机器人回复结果。

    返回规则：
    - 命中成功关键词 -> success
    - 命中已签到关键词 -> already_done
    - 超时或文案不明确 -> unknown
    """

    deadline = now_local() + timedelta(seconds=timeout_seconds)
    seen_message_ids: set[int] = set()

    # 先记录当前最近消息 ID，后续优先观察“点击之后新出现的消息”。
    for message in await get_recent_bot_messages(client, bot_username):
        seen_message_ids.add(message.id)

    while now_local() < deadline:
        recent_messages = await get_recent_bot_messages(client, bot_username)

        for message in recent_messages:
            text = message.raw_text or ""
            is_new_message = message.id not in seen_message_ids

            # 如果机器人编辑了旧消息而不是发新消息，这里的文本仍然会被检测到。
            if contains_keyword(text, ALREADY_DONE_KEYWORDS):
                return CheckinResult("already_done", text[:200] or "检测到已签到提示")

            if contains_keyword(text, SUCCESS_KEYWORDS):
                return CheckinResult("success", text[:200] or "检测到签到成功提示")

            if is_new_message and text:
                logging.info("收到机器人新消息，但未命中关键词: %s", text[:120])

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return CheckinResult("unknown", "点击后未在限定时间内识别到明确结果，请手动查看机器人回复。")


async def run_checkin_once(client: TelegramClient, state: dict) -> CheckinResult:
    """
    执行一次完整签到流程。

    流程：
    1. 本地状态防重
    2. Telegram 今日消息防重
    3. 发 /start
    4. 找按钮
    5. 点击按钮
    6. 等待机器人结果
    """

    bot_username = normalize_bot_username(BOT_USERNAME)
    today_str = now_local().strftime("%Y-%m-%d")

    if USE_LOCAL_STATE_GUARD:
        last_success_date = state.get("last_success_date")
        if last_success_date == today_str:
            return CheckinResult("already_done", "本地状态显示今天已经成功执行过，跳过重复签到。")

    if CHECK_BEFORE_SIGN:
        today_status = await detect_today_status(client, bot_username)
        if today_status is not None:
            return today_status

    previous_message_ids = {
        message.id for message in await get_recent_bot_messages(client, bot_username)
    }

    logging.info("开始向机器人 %s 发送 /start", bot_username)
    await client.send_message(bot_username, "/start")

    button_message = await wait_for_button_message(
        client=client,
        bot_username=bot_username,
        timeout_seconds=START_RESPONSE_TIMEOUT,
        previous_message_ids=previous_message_ids,
    )
    if button_message is None:
        return CheckinResult("failed", "发送 /start 后未找到包含目标按钮的消息。")

    logging.info("已找到目标按钮所在消息: %s", summarize_message(button_message))
    await click_target_button(button_message)

    result = await wait_for_result_after_click(
        client=client,
        bot_username=bot_username,
        timeout_seconds=CLICK_RESULT_TIMEOUT,
    )
    return result


def update_state_after_result(state: dict, result: CheckinResult) -> None:
    """
    根据本次结果更新本地状态文件。
    只有 success / already_done 才会把今天标记为已处理，避免失败后误跳过。
    """

    today_str = now_local().strftime("%Y-%m-%d")

    state["last_run_date"] = today_str
    state["last_status"] = result.status
    state["last_detail"] = result.detail
    state["updated_at"] = now_local().isoformat()

    if result.status in {"success", "already_done"}:
        state["last_success_date"] = today_str

    save_state(state)


def get_next_run_time() -> datetime:
    """
    计算下一次应该执行的时间点。
    如果今天的目标时间已经过去，就自动排到明天。
    """

    current = now_local()
    target_clock = datetime.strptime(DAILY_RUN_TIME, "%H:%M")
    target = current.replace(
        hour=target_clock.hour,
        minute=target_clock.minute,
        second=0,
        microsecond=0,
    )

    if target <= current:
        target += timedelta(days=1)

    return target


async def run_daily_loop(client: TelegramClient) -> None:
    """
    持续运行模式：
    脚本会一直挂着，每天到指定时间执行一次签到。

    备注：
    GitHub Actions 不建议使用这个模式，因为 Actions 更适合由 cron 每天触发一次。
    """

    state = load_state()

    while True:
        next_run = get_next_run_time()
        wait_seconds = max(1, int((next_run - now_local()).total_seconds()))
        logging.info("下一次执行时间: %s，需要等待 %s 秒。", next_run.isoformat(), wait_seconds)
        await asyncio.sleep(wait_seconds)

        try:
            result = await run_checkin_once(client, state)
            logging.info("本次签到结果: %s | %s", result.status, result.detail)
            update_state_after_result(state, result)
        except Exception as exc:
            logging.exception("本次签到流程出现异常: %s", exc)

        # 多等 2 秒是为了确保如果时间非常贴近边界，不会在同一分钟被重复触发。
        await asyncio.sleep(2)


async def run_single_check(client: TelegramClient) -> CheckinResult:
    """
    单次执行模式：
    更适合 Windows 任务计划程序和 GitHub Actions。
    """

    state = load_state()
    result = await run_checkin_once(client, state)
    update_state_after_result(state, result)
    return result


async def async_main(run_once: bool) -> None:
    """程序异步入口。"""

    validate_config()
    setup_logging()

    client = build_client()
    await ensure_login(client)

    try:
        if run_once:
            result = await run_single_check(client)
            logging.info("单次执行完成: %s | %s", result.status, result.detail)
        else:
            await run_daily_loop(client)
    finally:
        await client.disconnect()


def parse_args() -> argparse.Namespace:
    """
    命令行参数：
    - 默认不带参数：进入长期运行模式，每天自动执行
    - --once：只执行一次，适合手动运行、计划任务、GitHub Actions
    """

    parser = argparse.ArgumentParser(description="Telegram 机器人自动签到脚本")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一次签到流程，不进入每日循环。",
    )
    return parser.parse_args()


def main() -> None:
    """同步入口。"""

    args = parse_args()
    asyncio.run(async_main(run_once=args.once))


if __name__ == "__main__":
    main()
