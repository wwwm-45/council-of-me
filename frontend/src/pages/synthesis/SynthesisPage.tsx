import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  completeReflection,
  getReflectionTrace,
  getSynthesis,
  streamSynthesis,
  type ReflectionTraceResponse,
  type SynthesisResponse,
} from '../../api/client';
import { getSession as getStore, setSession } from '../../store/session';
import { readStageCache, writeStageCache } from '../../store/stageCache';
import DataSidebar from './DataSidebar';
import DialoguePanel from './DialoguePanel';
import { ImmersiveDrawer } from './ImmersiveViews';
import { buildLandscapeModel } from './landscapeModel';
import LandscapeMap from './LandscapeMap';
import NodeDetailPanel from './NodeDetailPanel';
import ReflectionTracePanel from './ReflectionTrace';
import { useReflectionJourney } from './useReflectionJourney';
import type {
  ImmersivePanelMode,
  NodeType,
  ReflectionExplorationView,
  ReflectionPathId,
  SelectedNode,
} from './types';

function resolveNodeType(data: SynthesisResponse, nodeId: string): NodeType | null {
  if (nodeId === 'center') return 'center';
  if (data.core_tensions.some((item) => item.tension_id === nodeId)) return 'tension';
  if (data.voice_positions.some((item) => item.agent_id === nodeId)) return 'voice';
  if (data.agent_evolutions.some((item) => item.agent_id === nodeId)) return 'voice';
  if (nodeId.startsWith('consensus-')) return 'consensus';
  if (nodeId.startsWith('productive-')) return 'productive';
  if (nodeId.startsWith('irreducible-')) return 'irreducible';
  return null;
}

function resolveNodeLabel(data: SynthesisResponse, selectedNode: SelectedNode): string {
  if (selectedNode.id === 'center') return data.dilemma_text || '核心议题';
  if (selectedNode.type === 'tension') {
    return data.core_tensions.find((item) => item.tension_id === selectedNode.id)?.name ?? selectedNode.id;
  }
  if (selectedNode.type === 'voice') {
    return data.voice_positions.find((item) => item.agent_id === selectedNode.id)?.agent_name
      ?? data.agent_evolutions.find((item) => item.agent_id === selectedNode.id)?.agent_name
      ?? selectedNode.id;
  }
  if (selectedNode.id.startsWith('consensus-')) {
    const index = Number.parseInt(selectedNode.id.split('-')[1] ?? '', 10);
    return data.consensus_areas[index]?.description ?? selectedNode.id;
  }
  if (selectedNode.id.startsWith('irreducible-')) {
    const index = Number.parseInt(selectedNode.id.split('-')[1] ?? '', 10);
    return data.irreducible_differences[index]?.description ?? selectedNode.id;
  }
  if (selectedNode.id.startsWith('productive-')) {
    const index = Number.parseInt(selectedNode.id.split('-')[1] ?? '', 10);
    return data.productive_tensions[index]?.description ?? selectedNode.id;
  }
  return selectedNode.id;
}

function createNodeRef(data: SynthesisResponse, node: SelectedNode) {
  return {
    node_id: node.id,
    node_type: node.type,
    node_label: resolveNodeLabel(data, node),
  };
}

function resolveRecommendedPath(exploration: ReflectionExplorationView | null): ReflectionPathId {
  if (exploration?.selected_path) {
    return exploration.selected_path as ReflectionPathId;
  }
  const feelings = new Set(exploration?.feelings ?? []);
  if (feelings.has('push_back') || feelings.has('surprise')) return 'assumption';
  if (exploration?.node_type === 'voice' && (feelings.has('seen') || feelings.has('wordless'))) return 'protective';
  if (exploration?.explicit_insight) return 'action';
  return 'emotional';
}

function pathReason(path: ReflectionPathId, exploration: ReflectionExplorationView | null) {
  if (exploration?.selected_path) {
    return '沿着你已经打开的入口继续往下走。';
  }
  if (path === 'assumption') {
    return '你刚才标记了明显的推拒或意外，从假设切入会更快。';
  }
  if (path === 'protective') {
    return '这个节点像是在替你守住什么，先看见保护意图。';
  }
  if (path === 'action') {
    return '这里已经有洞察了，可以轻轻带向下一步。';
  }
  return '先停在感觉里，不急着立刻解释它。';
}

function resolveTraceNodeOrder(
  reflectionState: ReturnType<typeof useReflectionJourney>['reflectionState'],
  trace: ReflectionTraceResponse | null,
) {
  const order = trace?.exploration_order ?? reflectionState?.exploration_order ?? [];
  const nodeIdByExplorationId = new Map<string, string>();

  for (const exploration of reflectionState?.explorations ?? []) {
    if (exploration.exploration_id) {
      nodeIdByExplorationId.set(exploration.exploration_id, exploration.node_id);
    }
  }
  for (const insight of trace?.insights ?? []) {
    if (insight.exploration_id) {
      nodeIdByExplorationId.set(insight.exploration_id, insight.node_id);
    }
  }

  return order
    .map((explorationId) => nodeIdByExplorationId.get(explorationId) ?? null)
    .filter((nodeId): nodeId is string => Boolean(nodeId));
}

function createClientTurnId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `client-${Date.now()}`;
}

interface SynthesisPageCache {
  data: SynthesisResponse | null;
  loading: boolean;
  loadingStage: string;
  selectedNode: SelectedNode | null;
  trace: ReflectionTraceResponse | null;
  reflectionEnabled: boolean;
}

export default function SynthesisPage() {
  const navigate = useNavigate();
  const sid = getStore().sessionId;
  const cached = readStageCache<SynthesisPageCache>(sid, 'synthesis');
  const [data, setData] = useState<SynthesisResponse | null>(() => cached?.data ?? null);
  const [loading, setLoading] = useState(() => cached?.data ? false : (cached?.loading ?? true));
  const [loadingStage, setLoadingStage] = useState<string>(() => cached?.loadingStage ?? '');
  const [selectedNode, setSelectedNode] = useState<SelectedNode | null>(() => cached?.selectedNode ?? null);
  const [trace, setTrace] = useState<ReflectionTraceResponse | null>(() => cached?.trace ?? null);
  const traceBaselineInsightsRef = useRef<Record<string, string>>(
    cached?.trace
      ? Object.fromEntries(cached.trace.insights.map((item) => [item.node_id, item.insight ?? '']))
      : {},
  );
  const [reflectionEnabled] = useState(() => {
    if (typeof cached?.reflectionEnabled === 'boolean') {
      return cached.reflectionEnabled;
    }
    const status = getStore().status;
    return status === 'reflecting' || status === 'closing';
  });
  const reflectionActive = reflectionEnabled || getStore().status === 'reflecting' || getStore().status === 'closing';
  const {
    beginDialogue,
    continueDialogue,
    persistInsight,
    reflectionState,
    viewMode,
    viewedNodeIds,
    markNodeViewed,
    saveFeelings,
    setViewMode,
  } = useReflectionJourney(sid, reflectionActive);
  const lastRestoredNodeIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sid) {
      navigate('/');
      return;
    }
    if (data) {
      return;
    }
    const controller = new AbortController();

    streamSynthesis(sid, {
      onStageStart: (info) => setLoadingStage(info.label),
      onStageEnd: () => {},
      onComplete: (result) => { setData(result); setLoading(false); },
      onCached: (result) => { setData(result); setLoading(false); },
      onError: () => {
        getSynthesis(sid).then(setData).catch(console.error).finally(() => setLoading(false));
      },
    }, controller.signal).catch(() => {
      if (!controller.signal.aborted) {
        getSynthesis(sid).then(setData).catch(console.error).finally(() => setLoading(false));
      }
    });

    return () => controller.abort();
  }, [data, navigate, sid]);

  useEffect(() => {
    if (!sid) return;
    writeStageCache<SynthesisPageCache>(sid, 'synthesis', {
      data,
      loading,
      loadingStage,
      selectedNode,
      trace,
      reflectionEnabled,
    });
  }, [data, loading, loadingStage, reflectionEnabled, selectedNode, sid, trace]);

  useEffect(() => {
    if (!data || selectedNode || !reflectionState?.current_node_id) return;
    if (lastRestoredNodeIdRef.current === reflectionState.current_node_id) return;
    const restoredType = resolveNodeType(data, reflectionState.current_node_id);
    if (!restoredType) return;
    lastRestoredNodeIdRef.current = reflectionState.current_node_id;
    const timer = window.setTimeout(() => {
      setSelectedNode({ id: reflectionState.current_node_id, type: restoredType });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [data, reflectionState?.current_node_id, selectedNode]);

  useEffect(() => {
    if (!sid || viewMode !== 'trace' || trace) return;
    let cancelled = false;
    getReflectionTrace(sid)
      .then((payload) => {
        if (cancelled) return;
        traceBaselineInsightsRef.current = Object.fromEntries(
          payload.insights.map((item) => [item.node_id, item.insight ?? '']),
        );
        setTrace(payload);
      })
      .catch(console.error);
    return () => {
      cancelled = true;
    };
  }, [sid, trace, viewMode]);

  function finishToClosure() {
    setSession({ status: 'closing' });
    navigate('/closure');
  }

  if (loading || !data) {
    // Full-screen immersive loader so the debate → synthesis hand-off stays on
    // the dark star-map shell instead of flashing the old light Layout chrome.
    return (
      <main className="synthesis-landscape fixed inset-0 z-50 flex flex-col items-center justify-center overflow-hidden bg-slate-950 text-slate-100 animate-fade-in">
        <div className="synthesis-landscape__background absolute inset-0" aria-hidden="true" />
        <div className="relative z-10 flex flex-col items-center gap-5">
          <div className="relative h-16 w-16">
            <div className="absolute inset-0 rounded-full border border-cyan-200/15" />
            <div className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-cyan-300/80 border-r-cyan-300/40" />
            <div className="absolute inset-[30%] animate-pulse rounded-full bg-cyan-300/30 blur-[2px]" />
          </div>
          <p className="text-sm tracking-[0.08em] text-slate-300">
            {loadingStage || '正在编织你的内心图景…'}
          </p>
        </div>
      </main>
    );
  }

  const model = buildLandscapeModel(data);
  const exploredNodeIds = (reflectionState?.explorations ?? [])
    .filter((item) => item.max_layer > 0 || item.feelings.length > 0 || Boolean(item.explicit_insight))
    .map((item) => item.node_id);
  const selectedExploration = selectedNode
    ? ((reflectionState?.explorations.find((item) => item.node_id === selectedNode.id) ?? null) as ReflectionExplorationView | null)
    : null;
  const dialogueExploration = ((selectedNode
    ? reflectionState?.explorations.find((item) => item.node_id === selectedNode.id)
    : reflectionState?.explorations.find((item) => item.exploration_id === reflectionState.current_exploration_id)
  ) ?? null) as ReflectionExplorationView | null;
  const selectedPath = resolveRecommendedPath(dialogueExploration);
  const traceOrderNodeIds = resolveTraceNodeOrder(reflectionState, trace);

  // One persistent full-screen landscape shell serves every loaded state. The
  // right drawer mode is derived from the reflection journey: dialogue/trace come
  // straight from the journey view mode, an explore drawer opens for any selected
  // node, otherwise the drawer stays closed and only the map + left data sidebar show.
  const panelMode: ImmersivePanelMode = viewMode === 'dialogue' || viewMode === 'trace'
    ? viewMode
    : selectedNode ? 'explore' : 'closed';

  function handleNodeSelect(node: SelectedNode | null) {
    // While the deep dialogue/trace drawer is open the map is a contextual
    // backdrop only; selecting a different node there would desync the drawer.
    if (viewMode === 'dialogue' || viewMode === 'trace') return;
    setSelectedNode(node);
    if (!node || !data) return;
    lastRestoredNodeIdRef.current = node.id;
    if (!reflectionActive) return;
    markNodeViewed(createNodeRef(data, node));
  }

  async function handlePathSelect(nextPath: ReflectionPathId) {
    if (!data || !selectedNode) return;
    await beginDialogue(
      createNodeRef(data, selectedNode),
      nextPath,
      dialogueExploration?.feelings ?? [],
    );
  }

  async function handleDialogueSubmit(content: string) {
    if (!dialogueExploration?.exploration_id) return;
    await continueDialogue(dialogueExploration.exploration_id, content, createClientTurnId());
  }

  async function handleSaveInsight(content: string) {
    if (!data || !selectedNode || !content.trim()) return;
    await persistInsight(createNodeRef(data, selectedNode), content.trim());
  }

  async function handleFinishNode() {
    if (!sid) return;
    const nextTrace = await getReflectionTrace(sid);
    traceBaselineInsightsRef.current = Object.fromEntries(
      nextTrace.insights.map((item) => [item.node_id, item.insight ?? '']),
    );
    setTrace(nextTrace);
    setViewMode('trace');
  }

  function handleTraceInsightChange(nodeId: string, insight: string) {
    setTrace((current) => {
      if (!current) return current;
      return {
        ...current,
        insights: current.insights.map((item) => (
          item.node_id === nodeId ? { ...item, insight } : item
        )),
      };
    });
  }

  async function handleProceedToClosure() {
    if (!sid || !data) return;
    for (const item of trace?.insights ?? []) {
      const nextInsight = (item.insight ?? '').trim();
      const baselineInsight = (traceBaselineInsightsRef.current[item.node_id] ?? '').trim();
      if (!nextInsight || nextInsight === baselineInsight) {
        continue;
      }
      await persistInsight({
        node_id: item.node_id,
        node_type: resolveNodeType(data, item.node_id) ?? 'voice',
        node_label: item.node_label,
      }, nextInsight);
    }
    await completeReflection(sid);
    setSession({ status: 'closing' });
    navigate('/closure');
  }

  function handleClosePanel() {
    // Closing the dialogue or trace drawer returns to the read-only explore view
    // (keeping the node if one is still selected); closing explore clears the node.
    if (viewMode === 'dialogue' || viewMode === 'trace') {
      setViewMode('explore');
      return;
    }
    setSelectedNode(null);
  }

  const drawerTitle = selectedNode ? resolveNodeLabel(data, selectedNode) : '反思轨迹';

  let panelContent: ReactNode = null;
  if (panelMode === 'explore' && selectedNode) {
    panelContent = (
      <NodeDetailPanel
        nodeId={selectedNode.id}
        nodeType={selectedNode.type}
        data={data}
        reflectionMode={reflectionActive}
        reflectionExploration={selectedExploration}
        onFeelingsChange={reflectionActive
          ? (feelings) => saveFeelings(createNodeRef(data, selectedNode), feelings)
          : undefined}
        onEnterDialogue={reflectionActive
          ? () => beginDialogue(
            createNodeRef(data, selectedNode),
            resolveRecommendedPath(selectedExploration),
            selectedExploration?.feelings ?? [],
          )
          : undefined}
      />
    );
  } else if (panelMode === 'dialogue' && dialogueExploration) {
    panelContent = (
      <DialoguePanel
        nodeLabel={dialogueExploration.node_label}
        selectedPath={selectedPath}
        currentLayer={reflectionState?.current_layer ?? dialogueExploration.current_layer ?? 1}
        turns={dialogueExploration.dialogue}
        recommendationReason={pathReason(selectedPath, dialogueExploration)}
        onPathSelect={handlePathSelect}
        onSubmit={handleDialogueSubmit}
        onSaveInsight={handleSaveInsight}
        onFinishNode={handleFinishNode}
        onBackToExplore={handleClosePanel}
      />
    );
  } else if (panelMode === 'trace' && trace) {
    panelContent = (
      <ReflectionTracePanel
        trace={trace}
        onInsightChange={handleTraceInsightChange}
        onProceed={handleProceedToClosure}
      />
    );
  }

  return (
    <main className="synthesis-landscape fixed inset-0 z-50 overflow-hidden bg-slate-950 text-slate-100 animate-fade-in">
      <div className="synthesis-landscape__background absolute inset-0" aria-hidden="true" />
      <div className="absolute inset-0 z-10 flex items-center justify-center p-4">
        <LandscapeMap
          model={model}
          onNodeSelect={handleNodeSelect}
          selectedNodeId={selectedNode?.id ?? null}
          mode={viewMode}
          viewedNodeIds={viewedNodeIds}
          exploredNodeIds={exploredNodeIds}
          traceOrder={traceOrderNodeIds}
        />
      </div>

      <DataSidebar data={data} immersiveActive={panelMode !== 'closed'} onFinish={finishToClosure} />

      {panelMode !== 'closed' && panelContent ? (
        <ImmersiveDrawer mode={panelMode} title={drawerTitle} onClose={handleClosePanel}>
          {panelContent}
        </ImmersiveDrawer>
      ) : null}
    </main>
  );
}
