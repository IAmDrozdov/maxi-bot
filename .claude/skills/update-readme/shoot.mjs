// Shoots the README's Mini App pictures from the local demo app: node shoot.mjs [--only=a,b]
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, rmSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PROFILES, connect, newPage } from './cdp.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const launcher = join(root, 'tools', 'miniapp_local.py')
const urlFile = join(root, '.local', 'miniapp_url.txt')
const profileFile = join(root, '.local', 'miniapp-local', 'profile')
const outDir = join(root, '.local', 'readme-shots')
const MAX_BYTES = 400 * 1024

const only = (process.argv.find(a => a.startsWith('--only='))?.slice(7) ?? '').split(',').filter(Boolean)

// A Place and Mode taken from the top bar's menu.
const goTo = async (page, mode, nth) => {
  await page.click('.place')
  await page.click('.seg2 button', mode, nth)
}

const thumbsLoaded = `[...document.querySelectorAll('img.thumb')].every(i => i.complete && i.naturalWidth > 0)`

async function notesList(page) {
  await page.open()
  await page.click('.rail .mark', '🍿')
  await page.until(thumbsLoaded, 'the covers')
  await page.settle()
}

const SCENES = [
  ['notes-list', 'notes-list.png', notesList],
  ['place-menu', 'place-menu.png', async page => {
    await notesList(page)
    await page.click('.place') // the Places and Modes menu, over the list
  }],
  ['notes-search', 'notes-search.png', async page => {
    await page.open()
    await page.click('button[aria-label="Поиск"]')
    await page.eval(`(() => { const i = document.querySelector('input[type=search]'); i.value = 'как'; i.dispatchEvent(new Event('input', { bubbles: true })) })()`)
    // The rail is hidden only while results stand in for the Section's list.
    await page.until(`document.querySelector('nav.rail')?.hidden && document.querySelectorAll('.row-wrap').length >= 3`, 'the search results')
    await page.until(thumbsLoaded, 'the covers')
    await page.eval('document.activeElement?.blur()')
    await page.settle()
  }],
  ['notes-sections', 'notes-sections.png', async page => {
    await page.open()
    await page.click('.rail .mark', '✏️') // «Правка секций»: the Sections, to reorder
    await page.until(`document.querySelector('.group-edit')`, 'the Sections list')
  }],
  ['notes-section-form', 'notes-section-form.png', async page => {
    await page.open()
    await page.click('.rail .mark', '✏️')
    await page.click('.group-edit', { has: 'Учёба', within: '.group' })
    await page.until(`document.body.innerText.includes('Подсказка для разбора')`, 'the Section form')
    await page.eval('document.activeElement?.blur()')
  }],
  ['notes-row-menu', 'notes-row-menu.png', async page => {
    await notesList(page)
    await page.click('.row .more', null, 0)
  }],
  ['notes-item', 'notes-item.png', async (page) => {
    await page.open()
    await page.click('.rail .mark', '🍿')
    const id = await page.eval(`(${page.find('.row-wrap', { has: 'трансформер' }, 0)}).dataset.id`)
    await page.reopen({ query: `?item=${id}` })
    await page.until(`document.body.innerText.includes('Описание')`, 'the Item view')
    await page.click('summary', 'Описание') // the fetched description, open
  }],
  ['notes-dashboard', 'notes-dashboard.png', async (page, profile) => {
    await page.open()
    await goTo(page, 'Дашборд', 0)
    await page.until(`document.querySelector('.headline-n')?.textContent.trim() === '${profile.todo}'`, 'the «Сделать» count')
  }],
  ['diary-day', 'diary-day.png', async page => {
    await page.open()
    await goTo(page, 'Записи', 1)
    await page.until(`document.body.innerText.includes('Тренировка')`, 'the Entries')
    await page.eval('document.activeElement?.blur()') // no caret in the picture
  }],
  ['diary-week', 'diary-week.png', async page => {
    await page.open()
    await goTo(page, 'Записи', 1)
    await page.click('#topbar .right button', null, 0)
    const entries = `[...document.querySelectorAll('.day-card')].filter(c => c.querySelector('.entry-row')).length`
    if ((await page.eval(entries)) < 4) await page.click('[aria-label="Предыдущая неделя"]')
    await page.eval(`(${page.find('h3', 'Итоги недели', 0)}).closest('section').scrollIntoView({ block: 'end' })`) // the summary, at the foot of the frame
  }],
  ['diary-dashboard', 'diary-dashboard.png', async page => {
    await page.open()
    await goTo(page, 'Дашборд', 1)
    if (new Date().getUTCMonth() < 2) await page.click('[aria-label="Предыдущий год"]')
  }],
  ['usage', 'usage.png', async page => {
    await page.open()
    await page.click('.place')
    await page.click('.menu-item', { has: 'Расходы' })
    await page.until(`document.querySelector('article.job')`, 'the job history')
    await page.click('.tile.clickable') // «Ошибки»: only failed jobs
    await page.click('article.job', { has: 'ошибка' })
    await page.eval('scrollTo(0, 0)')
  }],
]

// What every picture must satisfy: no 🧪 test data, and the Sections the demo seed made.
async function preflight(page, profile) {
  const text = await page.eval('document.body.innerText')
  if (text.includes('🧪')) throw new Error('a 🧪 label is on the page: the demo seed was not used')
  const caps = await page.eval(`[...document.querySelectorAll('.rail .cap')].map(e => e.textContent.trim())`)
  if (caps.length && JSON.stringify(caps) !== JSON.stringify(profile.rail)) {
    throw new Error(`rail is ${JSON.stringify(caps)}, the demo profile says ${JSON.stringify(profile.rail)}`)
  }
}

function startApp() {
  try {
    execFileSync('uv', ['run', 'python', launcher, 'up', '--demo'], { cwd: root, stdio: ['ignore', 'inherit', 'inherit'] })
  } catch {
    console.error('could not start the demo Mini App (is another instance running? nothing was stopped)')
    process.exit(1)
  }
}

const stopApp = () => execFileSync('uv', ['run', 'python', launcher, 'down'], { cwd: root, stdio: 'ignore' })

const wanted = SCENES.filter(([name]) => !only.length || only.includes(name))
if (!wanted.length) {
  console.error(`no scene matches --only; scenes: ${SCENES.map(s => s[0]).join(', ')}`)
  process.exit(1)
}

let browser = null
let cleaned = false
async function cleanup() {
  if (cleaned) return
  cleaned = true
  // Each step alone: a failed Chrome close must not leave the server or the signed URL behind.
  try { await browser?.close() } catch {}
  try { stopApp() } catch {}
  try { rmSync(urlFile, { force: true }) } catch {}
}
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => cleanup().finally(() => process.exit(130)))

startApp()
let failed = 0
try {
  const profile = JSON.parse(readFileSync(profileFile, 'utf8'))
  if (profile.profile !== 'demo') throw new Error('the running Mini App is not the demo profile')
  const launchUrl = readFileSync(urlFile, 'utf8').trim() // read in-process: it is a credential, never printed
  mkdirSync(outDir, { recursive: true }) // a partial run (--only) keeps the other pictures
  browser = await connect(join(outDir, '.chrome'))
  for (const [name, file, scene] of wanted) {
    const { page, targetId } = await newPage(browser.cdp, launchUrl, PROFILES.mac)
    const problems = []
    try {
      await scene(page, profile)
      await preflight(page, profile)
      await page.shot(join(outDir, file))
      const bytes = statSync(join(outDir, file)).size
      if (bytes > MAX_BYTES) problems.push(`${file} is ${bytes} bytes, over ${MAX_BYTES}`)
      console.log(`${file.padEnd(24)} ${bytes} bytes`)
    } catch (e) {
      problems.push(`${name}: ${e.message}`)
      await page.shot(join(outDir, `FAIL-${name}.png`)).catch(() => {})
    }
    problems.push(...page.errors.map(e => `${name}: ${e}`))
    for (const p of problems) console.error('FAIL ' + p)
    failed += problems.length
    await browser.cdp.send('Target.closeTarget', { targetId })
  }
} catch (e) {
  console.error('FAIL ' + e.message)
  failed += 1
} finally {
  await cleanup()
}
process.exit(failed ? 1 : 0)
