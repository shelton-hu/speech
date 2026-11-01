"""Exercise the delivered browser page against live speech services."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

root = Path(__file__).resolve().parents[1]

async def main():
    env = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1050, 'height': 1300})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto('http://127.0.0.1:18080')
        await page.fill('#token', env['APP_TOKEN'])
        await page.click('#check')
        await page.wait_for_function("document.querySelector('#status').textContent.includes('识别：')")
        await page.fill('#text', '你好，这是飞牛语音测试。')
        await page.click('#speak')
        await page.wait_for_function("document.querySelector('#download').hidden === false", timeout=120000)
        await page.wait_for_function("document.querySelector('#player').duration > 0")
        await page.set_input_files('#file', root / 'models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/test_wavs/zh.wav')
        await page.click('#recognize')
        await page.wait_for_function("document.querySelector('#asr').textContent.includes('录音 ') && !document.querySelector('#recognize').disabled", timeout=120000)
        if errors:
            raise RuntimeError(errors)
        await page.fill('#token', '')
        await page.screenshot(path=str(root / 'artifacts/verification/browser.png'), full_page=True)
        print(json.dumps({'status': await page.locator('#status').inner_text(), 'asr': await page.locator('#asr').inner_text(), 'tts': await page.locator('#tts').inner_text(), 'browser_errors': errors}, ensure_ascii=False))
        await browser.close()

asyncio.run(main())
