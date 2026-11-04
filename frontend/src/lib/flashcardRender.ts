// Draws a flashcard (flashcardText.CardContent) on a canvas and exports it as PNG / JPG. The canvas text engine shapes
// Bengali and Hindi like the page does; the card waits for those fonts before drawing.

import type { CardContent, Run } from './flashcardText'
import { clampLines, toRuns, wrapRuns } from './flashcardText'

export type CardMode = 'combined' | 'front' | 'back'
export type ImageType = 'image/png' | 'image/jpeg'

const WIDTH = 1600
const PAD = 80
const INNER = WIDTH - 2 * PAD
const FAMILY = '"Noto Sans", "Noto Sans Bengali", "Noto Sans Devanagari", Inter, "Segoe UI", system-ui, sans-serif'
const INK = '#18181b'
const MUTED = '#71717a'
const RULE = '#d4d4d8'
const MARK = '#fde68a'

type Ctx = CanvasRenderingContext2D
type Section = { height: number; draw: (ctx: Ctx, top: number) => void }

function font(size: number, bold = false): string {
    return `${bold ? 700 : 400} ${size}px ${FAMILY}`
}

function measurer(ctx: Ctx, size: number) {
    return (text: string, bold: boolean) => {
        ctx.font = font(size, bold)
        return ctx.measureText(text).width
    }
}

function lineWidth(ctx: Ctx, line: Run[], size: number): number {
    const measure = measurer(ctx, size)
    return line.reduce((sum, run) => sum + measure(run.text, run.bold), 0)
}

/** Draw one line of runs; bold runs inside an answer get a soft highlight behind them. */
function drawLine(ctx: Ctx, line: Run[], x: number, baseline: number, size: number, markBold: boolean) {
    let cursor = x
    for (const run of line) {
        ctx.font = font(size, run.bold)
        const width = ctx.measureText(run.text).width
        if (run.bold && markBold) {
            ctx.fillStyle = MARK
            ctx.fillRect(cursor - 2, baseline - size * 0.9, width + 4, size * 1.2)
        }
        ctx.fillStyle = INK
        ctx.fillText(run.text, cursor, baseline)
        cursor += width
    }
}

function label(ctx: Ctx, text: string, top: number) {
    ctx.font = font(22, true)
    ctx.fillStyle = MUTED
    ctx.letterSpacing = '4px'
    ctx.fillText(text, PAD, top + 22)
    ctx.letterSpacing = '0px'
}

function frontSection(ctx: Ctx, card: CardContent, minHeight: number): Section {
    const size = card.question.length > 90 ? 52 : 64
    const lines = clampLines(wrapRuns([{ text: card.question, bold: true }], INNER, measurer(ctx, size)), 5)
    const lineHeight = size * 1.3
    const textHeight = lines.length * lineHeight
    const height = Math.max(minHeight, textHeight + 2 * PAD + 40)
    return {
        height,
        draw: (c, top) => {
            label(c, 'QUESTION', top + 50)
            let baseline = top + (height - textHeight) / 2 + size + 10
            for (const line of lines) {
                drawLine(c, line, (WIDTH - lineWidth(c, line, size)) / 2, baseline, size, false)
                baseline += lineHeight
            }
        },
    }
}

function foldSection(): Section {
    return {
        height: 40,
        draw: (c, top) => {
            c.strokeStyle = MUTED
            c.lineWidth = 2
            c.setLineDash([14, 10])
            c.beginPath()
            c.moveTo(0, top + 20)
            c.lineTo(WIDTH, top + 20)
            c.stroke()
            c.setLineDash([])
            c.font = font(18)
            const text = 'fold'
            const width = c.measureText(text).width
            c.fillStyle = '#ffffff'
            c.fillRect(WIDTH - PAD - width - 16, top + 8, width + 16, 24)
            c.fillStyle = MUTED
            c.fillText(text, WIDTH - PAD - width - 8, top + 27)
        },
    }
}

function backSection(ctx: Ctx, card: CardContent, figure: HTMLImageElement | null): Section {
    const size = card.answer.length > 300 ? 34 : 40
    const lineHeight = size * 1.45
    const answer = clampLines(wrapRuns(toRuns(card.answer, card.highlight), INNER, measurer(ctx, size)), 12)

    const listSize = 30
    const listLineHeight = listSize * 1.4
    const markerWidth = 70
    const items = (card.list?.items ?? []).map((item, index) => {
        const marker = card.list?.ordered ? (card.list.markers?.[index] ?? `${index + 1}.`) : '•'
        return { marker, lines: clampLines(wrapRuns([{ text: item, bold: false }], INNER - markerWidth, measurer(ctx, listSize)), 2) }
    })
    const listHeight = items.reduce((sum, item) => sum + item.lines.length * listLineHeight + 10, items.length ? 24 : 0)

    let figureBox: { width: number; height: number } | null = null
    let captionLines: Run[][] = []
    if (figure && figure.naturalWidth && figure.naturalHeight) {
        const scale = Math.min(INNER / figure.naturalWidth, 420 / figure.naturalHeight, 2)
        figureBox = { width: figure.naturalWidth * scale, height: figure.naturalHeight * scale }
        captionLines = card.figure?.caption ? clampLines(wrapRuns([{ text: card.figure.caption, bold: false }], INNER, measurer(ctx, 24)), 2) : []
    }
    const figureHeight = figureBox ? 36 + figureBox.height + captionLines.length * 32 + 8 : 0

    const height = 90 + answer.length * lineHeight + listHeight + figureHeight + 50
    return {
        height,
        draw: (c, top) => {
            label(c, 'ANSWER', top + 40)
            let baseline = top + 90 + size
            for (const line of answer) {
                drawLine(c, line, PAD, baseline, size, true)
                baseline += lineHeight
            }
            let y = top + 90 + answer.length * lineHeight + (items.length ? 24 : 0)
            for (const item of items) {
                c.font = font(listSize, true)
                c.fillStyle = INK
                c.fillText(item.marker, PAD, y + listSize)
                for (const line of item.lines) {
                    drawLine(c, line, PAD + markerWidth, y + listSize, listSize, false)
                    y += listLineHeight
                }
                y += 10
            }
            if (figure && figureBox) {
                y += 36
                const x = (WIDTH - figureBox.width) / 2
                c.drawImage(figure, x, y, figureBox.width, figureBox.height)
                c.strokeStyle = RULE
                c.lineWidth = 2
                c.strokeRect(x, y, figureBox.width, figureBox.height)
                y += figureBox.height + 32
                for (const line of captionLines) {
                    c.font = font(24)
                    c.fillStyle = MUTED
                    const text = line.map((run) => run.text).join('')
                    c.fillText(text, (WIDTH - c.measureText(text).width) / 2, y)
                    y += 32
                }
            }
        },
    }
}

function footerSection(ctx: Ctx, card: CardContent): Section {
    const source = clampLines(wrapRuns([{ text: card.source, bold: true }], INNER - 200, measurer(ctx, 26)), 2)
    const height = 40 + source.length * 36 + 30
    return {
        height,
        draw: (c, top) => {
            c.fillStyle = RULE
            c.fillRect(PAD, top, INNER, 2)
            let baseline = top + 40 + 22
            for (const line of source) {
                drawLine(c, line, PAD, baseline, 26, false)
                baseline += 36
            }
            c.font = font(22, true)
            c.fillStyle = MUTED
            c.letterSpacing = '4px'
            const brand = 'OSISS'
            c.fillText(brand, WIDTH - PAD - c.measureText(brand).width, top + 62)
            c.letterSpacing = '0px'
        },
    }
}

async function waitForFonts(card: CardContent) {
    if (!('fonts' in document)) return
    const sample = card.language === 'bn' ? 'অআ' : card.language === 'hi' ? 'अआ' : 'Aa'
    try {
        await Promise.all([document.fonts.load(font(64, true), sample), document.fonts.load(font(40), sample)])
        await document.fonts.ready
    } catch {
        // Fonts that cannot be loaded fall back to the system font; the card still renders.
    }
}

function loadImage(src: string): Promise<HTMLImageElement | null> {
    return new Promise((resolve) => {
        const image = new Image()
        image.decoding = 'async'
        image.onload = () => resolve(image)
        image.onerror = () => resolve(null)
        image.src = src
    })
}

/** Draw a card: question and answer with a fold line between them, or one side on its own. */
export async function renderCard(card: CardContent, mode: CardMode = 'combined'): Promise<HTMLCanvasElement> {
    await waitForFonts(card)
    const figure = mode !== 'front' && card.figure ? await loadImage(card.figure.image) : null
    const scratch = document.createElement('canvas').getContext('2d')
    if (!scratch) throw new Error('Canvas is not available in this browser.')

    const sections: Section[] =
        mode === 'front'
            ? [frontSection(scratch, card, 760), footerSection(scratch, card)]
            : mode === 'back'
              ? [backSection(scratch, card, figure), footerSection(scratch, card)]
              : [frontSection(scratch, card, 460), foldSection(), backSection(scratch, card, figure), footerSection(scratch, card)]

    const height = Math.ceil(sections.reduce((sum, section) => sum + section.height, 0))
    const canvas = document.createElement('canvas')
    canvas.width = WIDTH
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Canvas is not available in this browser.')
    ctx.fillStyle = '#ffffff' // JPEG has no transparency
    ctx.fillRect(0, 0, WIDTH, height)
    ctx.textBaseline = 'alphabetic'
    let top = 0
    for (const section of sections) {
        section.draw(ctx, top)
        top += section.height
    }
    ctx.strokeStyle = INK
    ctx.lineWidth = 8
    ctx.strokeRect(4, 4, WIDTH - 8, height - 8)
    return canvas
}

export function canvasToBlob(canvas: HTMLCanvasElement, type: ImageType): Promise<Blob> {
    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not create the image.'))), type, type === 'image/jpeg' ? 0.92 : undefined)
    })
}

export function downloadBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export async function copyImage(blob: Blob) {
    if (!navigator.clipboard || typeof ClipboardItem === 'undefined') {
        throw new Error('Copying images is not supported here; save the card instead.')
    }
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })])
}

/** Render and save a card; returns the file name(s) written. */
export async function saveCard(card: CardContent, type: ImageType, mode: CardMode = 'combined'): Promise<string> {
    const extension = type === 'image/png' ? 'png' : 'jpg'
    const suffix = mode === 'combined' ? '' : `-${mode}`
    const filename = `flashcard-${card.slug}${suffix}.${extension}`
    downloadBlob(await canvasToBlob(await renderCard(card, mode), type), filename)
    return filename
}
