import os
import json
import asyncio
from pathlib import Path
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
ALLOWED_CHAT_ID = str(os.getenv("TG_CHAT_ID", "")).strip()

BASE_DIR = Path("./nexus_data")
PROFILES_DIR = BASE_DIR / "profiles"
CONFIG_FILE = BASE_DIR / "profiles_config.json"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

BLOCKED_DOMAINS = (
    "clarity.ms", "stripe.com", "stripe.network", "chargebee.com",
    "inmobi.com", "googletagmanager.com", "google-analytics.com",
    "doubleclick.net", "facebook.net", "churnkey.co"
)

ACTIVE_PAGE = None
ACTIVE_CONTEXT = None


def load_config() -> dict:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"default_profile": "p1", "profiles": {"p1": {"proxy": None}}}


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def parse_proxy(proxy_str: str) -> dict | None:
    if not proxy_str or proxy_str.lower() in ("none", "off", "clear"):
        return None
    parsed = urlparse(proxy_str if "://" in proxy_str else f"http://{proxy_str}")
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def is_authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(update.effective_chat.id) == ALLOWED_CHAT_ID


async def send_ss(bot, chat_id: int, page, caption: str):
    try:
        img_bytes = await page.screenshot(type="jpeg", quality=70)
        await bot.send_photo(chat_id=chat_id, photo=img_bytes, caption=caption[:1024])
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"{caption}\n(Screenshot failed: {e})")


async def wait_for_selector_instant(page, selector: str, timeout_ms: int = 25000) -> bool:
    js_observer = """
    ([selector, timeoutMs]) => new Promise((resolve) => {
        const isReady = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
            return true;
        };

        const check = () => {
            const els = document.querySelectorAll(selector);
            for (const el of els) {
                if (isReady(el)) return el;
            }
            return null;
        };

        if (check()) return resolve(true);

        const observer = new MutationObserver(() => {
            if (check()) {
                observer.disconnect();
                resolve(true);
            }
        });

        observer.observe(document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['style', 'class', 'disabled', 'hidden', 'aria-disabled']
        });

        setTimeout(() => {
            observer.disconnect();
            resolve(!!check());
        }, timeoutMs);
    })
    """
    return await page.evaluate(js_observer, [selector, timeout_ms])


async def run_automation(bot, chat_id: int, profile_name: str, target_url: str):
    global ACTIVE_PAGE, ACTIVE_CONTEXT

    cfg = load_config()
    prof_data = cfg["profiles"].setdefault(profile_name, {"proxy": None})
    save_config(cfg)

    profile_path = str(PROFILES_DIR / profile_name)
    proxy_cfg = parse_proxy(prof_data.get("proxy"))

    await bot.send_message(
        chat_id=chat_id,
        text=f"🚀 Starting Profile: `{profile_name}`\n🌐 URL: {target_url}\n🛡 Proxy: `{prof_data.get('proxy') or 'Direct'}`",
        parse_mode="Markdown"
    )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
            proxy=proxy_cfg,
            viewport={"width": 1280, "height": 720},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ACTIVE_CONTEXT = context

        async def route_filter(route):
            url = route.request.url.lower()
            if any(bad in url for bad in BLOCKED_DOMAINS):
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_filter)
        page = context.pages[0] if context.pages else await context.new_page()
        ACTIVE_PAGE = page

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_function("document.readyState === 'complete'", timeout=15000)

            steps = [
                ("Step 1: Watch Ads", '[data-testid="lv-membership-plan-option-wrapper-btn"]', 15000),
                ("Step 2: Continue", '[data-testid="action-wall-continue-action-btn"]', 10000),
                ("Step 3: Skip Ad (1/3)", '[data-testid="lv-lib-chip-text"], [data-testid="lv-chip"]', 25000),
                ("Step 4: Skip Ad (2/3)", '[data-testid="lv-lib-chip-text"], [data-testid="lv-chip"]', 25000),
                ("Step 5: Skip Ad (3/3)", '[data-testid="lv-lib-chip-text"], [data-testid="lv-chip"]', 25000),
                ("Step 6: Open Here", 'ui-card button:has-text("Open here"), button:has-text("Open here")', 15000),
            ]

            for label, selector, timeout in steps:
                found = await wait_for_selector_instant(page, selector, timeout_ms=timeout)
                if not found:
                    await send_ss(bot, chat_id, page, f"❌ FAILED at {label}\nButton didn't appear within {timeout//1000}s.")
                    return

                before_html_len = await page.evaluate("document.body.innerText.length")
                before_url = page.url

                locator = page.locator(selector).first
                await locator.click(timeout=5000)

                await asyncio.sleep(0.8)
                after_html_len = await page.evaluate("document.body.innerText.length")
                after_url = page.url

                verified = (after_url != before_url) or (abs(after_html_len - before_html_len) > 15)
                status_tag = "✅ VERIFIED" if verified else "⚠️ CLICKED (No immediate DOM shift)"
                await bot.send_message(chat_id=chat_id, text=f"{status_tag} · {label}")

            await send_ss(bot, chat_id, page, f"🎉 ALL STEPS COMPLETED!\nFinal URL: {page.url}")

        except Exception as e:
            await send_ss(bot, chat_id, page, f"💥 ERROR: {e}")
        finally:
            ACTIVE_PAGE = None
            ACTIVE_CONTEXT = None
            await context.close()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    msg = (
        "🤖 *NEXUS v3.0 Agent Ready*\n\n"
        "• `/run <url>` — Run on default profile\n"
        "• `/run <profile> <url>` — Run on specific profile (e.g. `p1`)\n"
        "• `/proxy <profile> <http://user:pass@ip:port>` — Set proxy (`none` to clear)\n"
        "• `/profiles` — List saved profiles\n"
        "• `/ss` — Get live screenshot right now"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/run <url>` or `/run p1 <url>`", parse_mode="Markdown")
        return

    cfg = load_config()
    if len(context.args) == 1:
        profile_name = cfg.get("default_profile", "p1")
        url = context.args[0]
    else:
        profile_name = context.args[0]
        url = context.args[1]

    asyncio.create_task(run_automation(context.bot, update.effective_chat.id, profile_name, url))


async def cmd_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/proxy p1 http://user:pass@ip:port` or `/proxy p1 none`", parse_mode="Markdown")
        return

    profile_name, proxy_val = context.args[0], context.args[1]
    cfg = load_config()
    cfg["profiles"].setdefault(profile_name, {})["proxy"] = None if proxy_val.lower() == "none" else proxy_val
    save_config(cfg)
    await update.message.reply_text(f"✅ Proxy updated for `{profile_name}`: `{proxy_val}`", parse_mode="Markdown")


async def cmd_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    cfg = load_config()
    lines = ["📂 *Saved Virtual Profiles:*"]
    for name, data in cfg["profiles"].items():
        lines.append(f"• `{name}` → Proxy: `{data.get('proxy') or 'Direct'}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_ss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return
    if ACTIVE_PAGE is None:
        await update.message.reply_text("No active browser running right now.")
        return
    await send_ss(context.bot, update.effective_chat.id, ACTIVE_PAGE, f"📸 Live View: {ACTIVE_PAGE.url}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN environment variable is missing!")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("proxy", cmd_proxy))
    app.add_handler(CommandHandler("profiles", cmd_profiles))
    app.add_handler(CommandHandler("ss", cmd_ss))
    print("NEXUS Telegram Agent is live...")
    app.run_polling()


if __name__ == "__main__":
    main()
