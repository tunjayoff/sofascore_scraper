/**
 * A modal dialog makes the rest of the page inert while it is open (05-web-ui.md 4.7, 4.9): what lies behind
 * the overlay can be neither clicked, focused nor found by assistive technology, and automation tools that
 * look for a text field by its role find the dialog's field only. The dialog itself is moved to the end of
 * `<body>` (a Teleport in UiDialog.vue), so a dialog opened from a table row is no longer inside that row:
 * a click in the dialog does not reach the row (which opened the row's page), and the row's styles (no
 * wrapping, right alignment) do not leak into it.
 *
 * Dialogs can stack (a confirmation opened from a dialog): only the newest is live, every other child of
 * `<body>` is inert, and closing it gives the one below its turn back.
 */
const stack: HTMLElement[] = []
/** Whether dialogs move to the end of `<body>`; the unit tests keep them where they were opened. */
let teleport = true
/** Elements made inert here, with whether they were inert before. */
const changed = new Map<Element, boolean>()

function apply() {
  for (const [el, was] of changed) if (!was) el.removeAttribute('inert')
  changed.clear()
  const top = stack[stack.length - 1]
  if (!top || typeof document === 'undefined') return
  for (const child of Array.from(document.body.children)) {
    if (child === top || child.contains(top) || child.tagName === 'SCRIPT' || child.tagName === 'TEMPLATE') continue
    changed.set(child, child.hasAttribute('inert'))
    child.setAttribute('inert', '')
  }
}

/** The dialog whose root is `el` opened: everything else becomes inert. */
export function openModal(el: HTMLElement) {
  if (!stack.includes(el)) stack.push(el)
  apply()
}

/** The dialog closed: the page (or the dialog below) is live again. */
export function closeModal(el: HTMLElement) {
  const i = stack.indexOf(el)
  if (i >= 0) stack.splice(i, 1)
  apply()
}

/** For tests: how many dialogs are open. */
export function openModals(): number {
  return stack.length
}

/** Whether a dialog opened now is moved to the end of `<body>`. */
export function teleportDialogs(): boolean {
  return teleport
}

/** For tests: keep dialogs where they are opened (`false`), or move them (`true`, the app's way). */
export function setTeleportDialogs(on: boolean) {
  teleport = on
}
