import { createContext } from 'react'
import { defaultUrlTransform } from 'react-markdown'

/** The checkout whose agent is speaking: its pictures are read through it. */
export const PictureRoot = createContext('')

/** react-markdown's default drops `C:\…` as an unknown scheme: an image's source is kept for `Picture` to judge. */
export const keepSources = (url: string, key: string) => key === 'src' ? url : defaultUrlTransform(url)
