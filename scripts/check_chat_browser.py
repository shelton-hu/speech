"""Browser workflow against the real gateway and local Postman dependency fixtures."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright
from check_chat import TOKEN, gateway

ROOT = Path(__file__).resolve().parents[1]


async def verify(base, configured=True):
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1050, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(base)
        await page.fill('#token', TOKEN)
        await page.click('#check')
        await page.wait_for_function("document.querySelector('#status').textContent.includes('大模型：')")
        if not configured:
            assert '尚未配置大模型' in await page.locator('#status').inner_text()
            await page.fill('#question', '你好')
            await page.click('#ask')
            await page.wait_for_function("!document.querySelector('#ask').disabled")
            assert '配置' in await page.locator('#chatStatus').inner_text()
            assert not errors, errors
            await browser.close()
            print('PASS: browser missing-configuration feedback')
            return
        await page.uncheck('#autoSpeak')
        await page.fill('#question', '我喜欢菠萝')
        await page.click('#ask')
        await page.wait_for_function("document.querySelector('#conversation').textContent.includes('模型回答')")
        await page.fill('#question', 'recall')
        await page.click('#ask')
        await page.wait_for_function("document.querySelector('#conversation').textContent.includes('你上一条说：我喜欢菠萝')")
        await page.fill('#question', 'error:401')
        await page.click('#ask')
        await page.wait_for_function("document.querySelector('#chatStatus').textContent.includes('被拒绝')")
        assert 'PRIVATE' not in await page.locator('#chatStatus').inner_text()
        assert 'error:401' not in await page.locator('#conversation').inner_text()
        await page.click('#newChat')
        assert await page.locator('#conversation').inner_text() == ''
        await page.set_input_files('#file', {'name': 'fixture.wav', 'mimeType': 'audio/wav', 'buffer': b'fixture'})
        await page.click('#askAudio')
        await page.wait_for_function("document.querySelector('#conversation').textContent.includes('录音问题')")
        await page.check('#autoSpeak')
        await page.fill('#question', 'long')
        await page.click('#ask')
        await page.wait_for_function("document.querySelector('#chatStatus').textContent.includes('播报完成')", timeout=30000)
        assert await page.locator('#replyPlayer').evaluate('(audio) => audio.duration > 0')
        await page.click('#replay')
        await page.wait_for_function("!document.querySelector('#stopReply').disabled")
        await page.click('#stopReply')
        assert '已停止' in await page.locator('#chatStatus').inner_text()
        # A TTS error must not erase a successful model answer or regenerate it.
        await page.route('**/v1/speech', lambda route: route.fulfill(
            status=503, content_type='application/json', body='{"error":{"message":"语音暂不可用"}}'))
        await page.fill('#question', '你好')
        await page.click('#ask')
        await page.wait_for_function("document.querySelector('#chatStatus').textContent.includes('文字回答已保留')")
        assert '模型回答' in await page.locator('#conversation').inner_text()
        assert await page.locator('#replay').is_enabled()
        await page.set_viewport_size({'width': 390, 'height': 844})
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        await page.fill('#token', '')
        out = ROOT / 'artifacts/verification'
        out.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out / 'chat-mobile.png'), full_page=True)
        await page.set_viewport_size({'width': 1050, 'height': 1000})
        await page.screenshot(path=str(out / 'chat-desktop.png'), full_page=True)
        assert not errors, errors
        await browser.close()
        print('PASS: browser text, history, errors, reset, audio question, chunked playback, stop, replay and mobile layout')


if __name__ == '__main__':
    with gateway() as client:
        asyncio.run(verify(str(client.base_url)))
    with gateway(LLM_API_KEY='') as client:
        asyncio.run(verify(str(client.base_url), configured=False))
