'use client'

import * as React from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useTranslations } from 'next-intl'
import * as Icons from 'lucide-react'
import { decisionKey, type RelationshipCandidate, type RelationshipDecision, type SchemaDiscoveryResult } from '@/lib/queries/pipeline'

type ColumnRole = 'pk' | 'fk' | 'both' | null

type TableNodeData = {
  name: string
  isPrimary: boolean
  rowCount: number
  columnCount: number
  columns: string[]
  columnRoles: Record<string, ColumnRole>
}

type TableFlowNode = Node<TableNodeData, 'table'>

function roleFor(table: string, column: string, rels: RelationshipCandidate[]): ColumnRole {
  const asFk = rels.some((r) => r.table_a === table && r.column_a === column)
  const asPk = rels.some((r) => r.table_b === table && r.column_b === column)
  if (asFk && asPk) return 'both'
  if (asPk) return 'pk'
  if (asFk) return 'fk'
  return null
}

function computeLayout(
  tables: SchemaDiscoveryResult['tables'],
  rels: RelationshipCandidate[]
): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {}
  if (tables.length === 0) return positions
  if (tables.length === 1) {
    positions[tables[0].name] = { x: 0, y: 0 }
    return positions
  }

  const fkCounts: Record<string, number> = {}
  const degree: Record<string, number> = {}
  for (const t of tables) {
    fkCounts[t.name] = 0
    degree[t.name] = 0
  }
  for (const r of rels) {
    if (r.table_a in fkCounts) fkCounts[r.table_a]++
    if (r.table_a in degree) degree[r.table_a]++
    if (r.table_b in degree) degree[r.table_b]++
  }

  const center = [...tables].sort(
    (a, b) => (fkCounts[b.name] - fkCounts[a.name]) || (degree[b.name] - degree[a.name]) || a.name.localeCompare(b.name)
  )[0].name

  positions[center] = { x: 0, y: 0 }
  const rest = tables.filter((t) => t.name !== center)

  if (rest.length === 1) {
    positions[rest[0].name] = { x: 420, y: 0 }
    return positions
  }

  const radius = Math.max(420, rest.length * 110)
  const step = (2 * Math.PI) / rest.length
  // Start at 0deg (right) rather than -90deg (top): for the common 2-dimension
  // star-schema case this spreads tables left/right instead of stacking them
  // vertically, which reads better in a wide card.
  rest.forEach((t, i) => {
    const angle = i * step
    positions[t.name] = { x: Math.round(Math.cos(angle) * radius), y: Math.round(Math.sin(angle) * radius) }
  })
  return positions
}

function RoleTag({ role }: { role: ColumnRole }) {
  const t = useTranslations('relationshipErd')
  if (!role) return null
  return (
    <span
      className="inline-flex items-center gap-0.5 rounded px-1 py-0 text-2xs font-semibold uppercase tracking-wide ring-1 ring-inset shrink-0"
      style={
        role === 'fk'
          ? { color: 'var(--info)', background: 'color-mix(in oklch, var(--info) 12%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in oklch, var(--info) 25%, transparent)' }
          : { color: 'var(--warning)', background: 'color-mix(in oklch, var(--warning) 12%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in oklch, var(--warning) 25%, transparent)' }
      }
    >
      {role === 'pk' && <Icons.KeyRound className="size-2.5" />}
      {role === 'fk' && <Icons.Link2 className="size-2.5" />}
      {role === 'both' && <><Icons.KeyRound className="size-2.5" /><Icons.Link2 className="size-2.5" /></>}
      {t(`roleTag.${role}`)}
    </span>
  )
}

function TableNode({ data }: NodeProps<TableFlowNode>) {
  const t = useTranslations('relationshipErd')
  return (
    <div className="w-[220px] rounded-lg border border-border/70 bg-card shadow-elevated overflow-hidden">
      <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-border/60 bg-muted/30">
        <Icons.Table2 className="size-3.5 text-muted-foreground shrink-0" />
        <span className="text-sm font-semibold truncate flex-1">{data.name}</span>
        {data.isPrimary && (
          <span className="text-2xs font-medium text-primary bg-primary/12 rounded px-1 py-0 shrink-0">{t('node.primaryBadge')}</span>
        )}
      </div>
      <div className="px-2.5 py-1 text-2xs text-muted-foreground border-b border-border/40">
        {t('node.rowsCols', { rows: data.rowCount, cols: data.columnCount })}
      </div>
      <div className="max-h-[180px] overflow-y-auto py-1">
        {data.columns.map((col) => (
          <div key={col} className="relative flex items-center gap-1.5 px-2.5 py-1 hover:bg-accent/30">
            <Handle
              type="target"
              position={Position.Left}
              id={`tgt:${col}`}
              isConnectable={false}
              className="!bg-border !size-1.5 !border-0"
            />
            <span className="font-mono text-2xs truncate flex-1 text-foreground/85">{col}</span>
            <RoleTag role={data.columnRoles[col] ?? null} />
            <Handle
              type="source"
              position={Position.Right}
              id={`src:${col}`}
              isConnectable={false}
              className="!bg-border !size-1.5 !border-0"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

const nodeTypes = { table: TableNode }

function effectiveStatus(r: RelationshipCandidate, decisions: Record<string, RelationshipDecision>): 'approved' | 'rejected' {
  return decisions[decisionKey(r)]?.status ?? (r.source === 'manual' ? 'approved' : r.confidence >= 0.5 ? 'approved' : 'rejected')
}

export interface RelationshipErdViewProps {
  tables: SchemaDiscoveryResult['tables']
  primaryTable?: string
  relationships: RelationshipCandidate[]
  decisions: Record<string, RelationshipDecision>
  onToggleStatus: (c: RelationshipCandidate, status: 'approved' | 'rejected') => void
}

function ErdCanvas({ tables, primaryTable, relationships, decisions, onToggleStatus }: RelationshipErdViewProps) {
  const t = useTranslations('relationshipErd')
  const [nodes, setNodes, onNodesChange] = useNodesState<TableFlowNode>([])
  const [edges, setEdges] = useEdgesState<Edge>([])
  const candidateByEdgeId = React.useRef<Map<string, RelationshipCandidate>>(new Map())

  const tableKey = tables.map((t) => t.name).join('|')

  const columnRolesByTable = React.useMemo(() => {
    const map: Record<string, Record<string, ColumnRole>> = {}
    for (const t of tables) {
      const roles: Record<string, ColumnRole> = {}
      for (const col of t.columns) roles[col] = roleFor(t.name, col, relationships)
      map[t.name] = roles
    }
    return map
  }, [tableKey, relationships])

  // Rebuild node positions only when the table set itself changes, so dragging
  // a table around isn't undone by an approve/reject click elsewhere.
  React.useEffect(() => {
    const positions = computeLayout(tables, relationships)
    const built: TableFlowNode[] = tables.map((t) => ({
      id: t.name,
      type: 'table',
      position: positions[t.name] ?? { x: 0, y: 0 },
      data: {
        name: t.name,
        isPrimary: t.name === primaryTable,
        rowCount: t.row_count,
        columnCount: t.column_count,
        columns: t.columns,
        columnRoles: columnRolesByTable[t.name] ?? {},
      },
    }))
    setNodes(built)
  }, [tableKey])

  // Column role badges (PK/FK) can change (e.g. a manual relationship is added)
  // without touching node positions.
  React.useEffect(() => {
    setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, columnRoles: columnRolesByTable[n.id] ?? {} } })))
  }, [columnRolesByTable, setNodes])

  React.useEffect(() => {
    const map = new Map<string, RelationshipCandidate>()
    const built: Edge[] = relationships.map((r) => {
      const key = decisionKey(r)
      map.set(key, r)
      const status = effectiveStatus(r, decisions)
      const approved = status === 'approved'
      const color = approved ? 'var(--success)' : 'var(--muted-foreground)'
      const lowConfidence = r.source !== 'manual' && r.confidence < 0.9
      return {
        id: key,
        source: r.table_a,
        sourceHandle: `src:${r.column_a}`,
        target: r.table_b,
        targetHandle: `tgt:${r.column_b}`,
        type: 'smoothstep',
        animated: false,
        style: {
          stroke: color,
          strokeWidth: 2,
          strokeDasharray: approved ? undefined : '4 4',
          opacity: approved ? 1 : 0.55,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
        label: lowConfidence ? '⚠' : undefined,
        labelStyle: { fill: 'var(--warning)', fontSize: 11 },
        labelBgStyle: { fill: 'var(--card)', fillOpacity: 0.85 },
      }
    })
    candidateByEdgeId.current = map
    setEdges(built)
  }, [relationships, decisions, setEdges])

  const handleEdgeClick = React.useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      const candidate = candidateByEdgeId.current.get(edge.id)
      if (!candidate) return
      const current = effectiveStatus(candidate, decisions)
      onToggleStatus(candidate, current === 'approved' ? 'rejected' : 'approved')
    },
    [decisions, onToggleStatus]
  )

  return (
    <div
      className="h-[440px] w-full"
      style={
        {
          '--xy-node-background-color': 'transparent',
          '--xy-node-border': 'none',
          '--xy-node-box-shadow': 'none',
          '--xy-controls-button-background-color': 'var(--card)',
          '--xy-controls-button-background-color-hover': 'var(--accent)',
          '--xy-controls-button-color': 'var(--foreground)',
          '--xy-controls-button-border-color': 'var(--border)',
          '--xy-minimap-background-color': 'var(--card)',
        } as React.CSSProperties
      }
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        onEdgeClick={handleEdgeClick}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} />
        {tables.length > 4 && (
          <MiniMap pannable zoomable nodeColor="var(--muted)" nodeStrokeColor="var(--border)" maskColor="color-mix(in oklch, var(--background) 75%, transparent)" />
        )}
        <Panel
          position="top-right"
          className="rounded-md border border-border/60 bg-card/90 backdrop-blur px-2.5 py-2 text-2xs space-y-1 shadow-elevated"
        >
          <div className="flex items-center gap-1.5"><span className="inline-block w-4 h-0 border-t-2 rounded" style={{ borderColor: 'var(--success)' }} /> {t('legend.approved')}</div>
          <div className="flex items-center gap-1.5"><span className="inline-block w-4 h-0 border-t-2 rounded border-dashed" style={{ borderColor: 'var(--muted-foreground)' }} /> {t('legend.rejected')}</div>
          <div className="flex items-center gap-1.5"><span style={{ color: 'var(--warning)' }}>⚠</span> {t('legend.lowConfidence')}</div>
        </Panel>
      </ReactFlow>
    </div>
  )
}

export function RelationshipErdView(props: RelationshipErdViewProps) {
  if (props.tables.length === 0) return null
  return (
    <ReactFlowProvider>
      <ErdCanvas {...props} />
    </ReactFlowProvider>
  )
}
