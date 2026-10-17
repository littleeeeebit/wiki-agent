/** A repository's own documents, as `repo_graph.picture` drew them. */
export type DocNode = {
  id: string
  kind: 'doc' | 'page' | 'module' | 'decision'
  title: string
  chars: number
  severity?: string
  triggers?: string[]
  injected?: boolean
}

/** A hub rule in `graph.build`'s shape — only the fields the map reads. */
export type RuleNode = {
  id: string
  label: string
  scope: string
  severity: string
  headline: string
  rule: string
  chars: number
  injected: boolean
  triggers: string[]
  layer: number
  status: Record<string, string>
}

/** One repository's map in two layers (`GET /api/graph?repo=`). */
export type MapData = {
  repo: string
  /** The repository is the hub: its hub layer is `graph.json` unchanged. */
  hub: boolean
  /** The hub's name, for reading a rule page's file. */
  wiki: string
  layers: {
    repo: { nodes: DocNode[]; edges: { a: string; b: string }[] }
    hub: { nodes: RuleNode[]; edges: { a: string; b: string; kind: 'link' | 'co' }[];
      ladder: { n: number; title: string }[] }
  }
  metrics: { pages: number; orphans: number; lint: number }
}

