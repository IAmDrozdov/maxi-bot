// Headless Chrome over raw CDP, just enough to drive the local Mini App for README pictures.
import { spawn } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export const sleep = ms => new Promise(r => setTimeout(r, ms))

export function launchChrome(profileDir) {
  rmSync(profileDir, { recursive: true, force: true })
  const proc = spawn(
    CHROME,
    ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, '--no-first-run',
      '--no-default-browser-check', '--lang=ru-RU', '--hide-scrollbars', '--disable-extensions', 'about:blank'],
    { stdio: ['ignore', 'ignore', 'pipe'] },
  )
  return new Promise((resolve, reject) => {
    let buf = ''
    proc.stderr.on('data', d => {
      buf += d
      const m = buf.match(/DevTools listening on (ws:\/\/\S+)/)
      if (m) resolve({ proc, ws: m[1] })
    })
    proc.on('error', e => reject(new Error(`cannot start chrome: ${e.message}`)))
    proc.on('exit', code => reject(new Error(`chrome exited ${code}: ${buf.slice(-300)}`)))
  })
}

export class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.handlers = new Set()
    ws.addEventListener('message', e => {
      const m = JSON.parse(e.data)
      if (!m.id) return this.handlers.forEach(h => h(m))
      const p = this.pending.get(m.id)
      this.pending.delete(m.id)
      if (m.error) p.reject(new Error(`${p.method}: ${m.error.message}`))
      else p.resolve(m.result)
    })
  }

  send(method, params = {}, sessionId) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params, sessionId }))
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method }))
  }
}

// The Mini App as Telegram for macOS shows it (docs: ~400x644), light theme.
export const PROFILES = {
  mac: { width: 400, height: 644, scheme: 'light' },
}

// Chrome plus its CDP connection; close() ends both.
export async function connect(profileDir) {
  const { proc, ws } = await launchChrome(profileDir)
  const socket = new WebSocket(ws)
  try {
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve)
      socket.addEventListener('error', () => reject(new Error('cannot connect to chrome')))
    })
  } catch (e) {
    proc.kill()
    rmSync(profileDir, { recursive: true, force: true })
    throw e
  }
  const close = async () => {
    try { socket.close() } catch {}
    proc.kill()
    await sleep(300)
    rmSync(profileDir, { recursive: true, force: true })
  }
  return { cdp: new Cdp(socket), close }
}

export class Page {
  constructor(cdp, sessionId, launchUrl) {
    this.cdp = cdp
    this.sessionId = sessionId
    this.launchUrl = launchUrl
    this.origin = new URL(launchUrl).origin
    this.inflight = new Set()
    this.errors = []
    cdp.handlers.add(m => m.sessionId === sessionId && this.on(m))
  }

  // A call the browser never answers names itself (the method only: a navigate's params carry the signed URL).
  send(method, params) {
    let timer
    const late = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`no answer to ${method}`)), 20000)
    })
    return Promise.race([this.cdp.send(method, params, this.sessionId), late]).finally(() => clearTimeout(timer))
  }

  on(m) {
    const p = m.params
    if (m.method === 'Network.requestWillBeSent') this.inflight.add(p.requestId)
    if (m.method === 'Network.loadingFinished' || m.method === 'Network.loadingFailed') this.inflight.delete(p.requestId)
    if (m.method === 'Page.javascriptDialogOpening') this.errors.push('dialog: ' + p.message)
    if (m.method === 'Runtime.exceptionThrown') {
      this.errors.push('exception: ' + (p.exceptionDetails.exception?.description ?? p.exceptionDetails.text).slice(0, 300))
    }
    if (m.method === 'Runtime.consoleAPICalled' && p.type === 'error') {
      this.errors.push('console.error: ' + JSON.stringify(p.args.map(a => a.value ?? a.description)).slice(0, 300))
    }
  }

  async setup(profile) {
    this.profile = profile
    await this.send('Page.enable')
    await this.send('Runtime.enable')
    await this.send('Network.enable')
    // The native date picker stops a headless page from drawing frames.
    await this.send('Page.addScriptToEvaluateOnNewDocument', {
      source: 'HTMLInputElement.prototype.showPicker = function () {}',
    })
    await this.send('Emulation.setDeviceMetricsOverride', {
      width: profile.width, height: profile.height, deviceScaleFactor: 2, mobile: false,
    })
    await this.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: profile.scheme }] })
    await this.send('Emulation.setTimezoneOverride', { timezoneId: 'UTC' }) // the demo seed's days are UTC days
    await this.send('Emulation.setLocaleOverride', { locale: 'ru-RU' })
  }

  // clear: start with empty storage (no remembered Place, no snapshots), so a scene never depends on another.
  async open({ query = '', clear = true } = {}) {
    this.inflight.clear()
    await this.send('Page.navigate', { url: 'about:blank' })
    if (clear) {
      const storageTypes = 'local_storage,session_storage,indexeddb,cache_storage'
      await this.send('Storage.clearDataForOrigin', { origin: this.origin, storageTypes })
    }
    const u = new URL(this.launchUrl)
    await this.send('Page.navigate', { url: `${u.origin}/${query}${u.hash}` })
    await this.settle()
  }

  reopen(opts = {}) {
    return this.open({ ...opts, clear: false })
  }

  async settle() {
    const end = Date.now() + 10000
    let quiet = 0
    await sleep(150)
    while (Date.now() < end) {
      const loading = await this.eval(
        `document.readyState !== 'complete' || [...document.querySelectorAll('main *')].some(e => e.children.length === 0 && e.getClientRects().length && e.textContent.trim() === 'Загрузка…')`,
      ).catch(() => true)
      quiet = this.inflight.size === 0 && !loading ? quiet + 1 : 0
      if (quiet >= 3) break
      await sleep(100)
    }
    // Two frames, so what the last step changed is drawn.
    await this.eval(
      'Promise.race([document.fonts.ready.then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))), new Promise(r => setTimeout(() => r(false), 1500))])',
    )
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text)
    return r.result.value
  }

  // Polls an expression until it is truthy; names what it waited for if it never is.
  async until(expression, what, ms = 10000) {
    const end = Date.now() + ms
    while (Date.now() < end) {
      if (await this.eval(expression).catch(() => false)) return
      await sleep(100)
    }
    throw new Error(`timed out waiting for ${what}`)
  }

  // text: null (any), a string (the whole normalized text), or { has, within } (contains, inside an ancestor).
  find(sel, text, nth) {
    return `(() => {
      const want = ${JSON.stringify(text)}
      const norm = s => s.replace(/\\s+/g, ' ').trim()
      const fits = e => {
        if (want === null) return true
        if (typeof want === 'string') return norm(e.textContent) === want
        const scope = want.within ? e.closest(want.within) : e
        return Boolean(scope) && norm(scope.textContent).includes(want.has)
      }
      return [...document.querySelectorAll(${JSON.stringify(sel)})].filter(e => e.getClientRects().length && fits(e))[${nth}]
    })()`
  }

  async locate(sel, text = null, nth = 0, scroll = true) {
    const at = await this.eval(`(() => {
      const el = ${this.find(sel, text, nth)}
      if (!el) return null
      if (${scroll}) el.scrollIntoView({ block: 'center', inline: 'nearest' })
      const b = el.getBoundingClientRect()
      return { x: b.left + b.width / 2, y: b.top + b.height / 2 }
    })()`)
    if (!at) throw new Error(`no visible element ${sel} ${JSON.stringify(text)} #${nth}`)
    return at
  }

  mouse(type, x, y, extra = {}) {
    return this.send('Input.dispatchMouseEvent', { type, x, y, ...extra })
  }

  async clickAt(x, y, { settle = true } = {}) {
    await this.mouse('mouseMoved', x, y)
    await this.mouse('mousePressed', x, y, { button: 'left', buttons: 1, clickCount: 1 })
    await this.mouse('mouseReleased', x, y, { button: 'left', clickCount: 1 })
    if (settle) await this.settle()
  }

  async click(sel, text = null, nth = 0, opts = {}) {
    const { x, y } = await this.locate(sel, text, nth)
    await this.clickAt(x, y, opts)
  }

  // The native Telegram BackButton, which a plain browser does not draw.
  async back() {
    await this.eval(`window.Telegram.WebView.receiveEvent('back_button_pressed', {})`)
    await this.settle()
  }

  // The viewport only, so every picture has the same frame.
  async shot(file) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(file, Buffer.from(r.data, 'base64'))
  }
}

// A fresh target with its own Page, set up for `profile`.
export async function newPage(cdp, launchUrl, profile) {
  const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' })
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true })
  const page = new Page(cdp, sessionId, launchUrl)
  await page.setup(profile)
  return { page, targetId }
}
