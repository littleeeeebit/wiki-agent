import { createContext } from 'react'

/** The open modal `<dialog>`, where a popup inside it must portal: a modal dialog sits in the top
 *  layer, so a popup portaled to `body` is drawn under it and never takes a click. */
export const Layer = createContext<HTMLElement | null>(null)
