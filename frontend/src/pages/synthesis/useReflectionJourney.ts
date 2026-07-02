import { startTransition, useEffect, useRef, useState } from 'react';
import {
  markReflectionFeeling,
  markReflectionViewed,
  respondReflectionDialogue,
  saveReflectionInsight,
  startReflection,
  startReflectionDialogue,
  type ReflectionDialoguePayload,
  type ReflectionDialogueTurn,
  type ReflectionPathId,
  type ReflectionServerState,
} from '../../api/client';
import { readStageCache, writeStageCache } from '../../store/stageCache';
import type { FeelingId, ReflectionViewMode } from './types';

type ReflectionNodeRef = {
  node_id: string;
  node_type: string;
  node_label: string;
};

function toViewMode(state: ReflectionServerState): ReflectionViewMode {
  if (state.phase === 'trace') return 'trace';
  if (state.phase === 'dialogue') return 'dialogue';
  return 'explore';
}

function createBaseState(sessionId: string): ReflectionServerState {
  return {
    session_id: sessionId,
    phase: 'explore',
    started_at: '',
    updated_at: '',
    nodes_viewed: [],
    explorations: [],
    current_exploration_id: '',
    current_node_id: '',
    dialogue: [],
    exploration_order: [],
    current_path: null,
    current_layer: 0,
    completed_at: null,
  };
}

function appendUnique<T>(items: T[], predicate: (item: T) => boolean, nextItem: T): T[] {
  return items.some(predicate) ? items : [...items, nextItem];
}

function ensureExploration(
  state: ReflectionServerState,
  node: ReflectionNodeRef,
) {
  const existingIndex = state.explorations.findIndex(
    (item) => item.node_id === node.node_id,
  );
  const existing = existingIndex >= 0 ? state.explorations[existingIndex] : null;
  return {
    existing,
    existingIndex,
    exploration: existing ?? {
      exploration_id: '',
      node_id: node.node_id,
      node_type: node.node_type,
      node_label: node.node_label,
      feelings: [],
      selected_path: '',
      current_layer: 0,
      max_layer: 0,
      explicit_insight: '',
      status: 'in_progress',
      dialogue: [],
    },
  };
}

function mergeDialoguePayload(
  current: ReflectionServerState | null,
  sessionId: string,
  node: ReflectionNodeRef,
  feelings: FeelingId[],
  payload: ReflectionDialoguePayload,
): ReflectionServerState {
  const base = current ?? createBaseState(sessionId);
  const { existing, existingIndex, exploration } = ensureExploration(base, node);
  const nextDialogue = appendUnique(
    exploration.dialogue,
    (turn) => turn.turn_id === payload.assistant_turn.turn_id,
    payload.assistant_turn,
  );

  const nextExploration = {
    ...exploration,
    exploration_id: payload.exploration_id,
    node_id: node.node_id,
    node_type: node.node_type,
    node_label: node.node_label,
    feelings: feelings.length > 0 ? feelings : existing?.feelings ?? [],
    selected_path: payload.selected_path,
    current_layer: payload.current_layer,
    max_layer: Math.max(payload.current_layer, exploration.max_layer ?? 0),
    status: payload.exploration_status,
    dialogue: nextDialogue,
  };

  const explorations = [...base.explorations];
  if (existingIndex >= 0) {
    explorations[existingIndex] = nextExploration;
  } else {
    explorations.push(nextExploration);
  }

  const nodesViewed = base.nodes_viewed.includes(node.node_id)
    ? base.nodes_viewed
    : [...base.nodes_viewed, node.node_id];

  return {
    ...base,
    phase: 'dialogue',
    current_exploration_id: payload.exploration_id,
    current_node_id: node.node_id,
    current_path: payload.selected_path,
    current_layer: payload.current_layer,
    nodes_viewed: nodesViewed,
    explorations,
    dialogue: nextDialogue,
  };
}

function mergeViewedNodeState(
  current: ReflectionServerState | null,
  sessionId: string,
  node: ReflectionNodeRef,
): ReflectionServerState {
  const base = current ?? createBaseState(sessionId);
  const { existingIndex, exploration } = ensureExploration(base, node);
  const nextExploration = {
    ...exploration,
    node_id: node.node_id,
    node_type: node.node_type,
    node_label: node.node_label,
  };
  const explorations = [...base.explorations];
  if (existingIndex >= 0) {
    explorations[existingIndex] = nextExploration;
  } else {
    explorations.push(nextExploration);
  }
  return {
    ...base,
    nodes_viewed: base.nodes_viewed.includes(node.node_id)
      ? base.nodes_viewed
      : [...base.nodes_viewed, node.node_id],
    current_node_id: node.node_id,
    explorations,
  };
}

function mergeDialogueResponse(
  current: ReflectionServerState | null,
  explorationId: string,
  content: string,
  clientTurnId: string,
  payload: ReflectionDialoguePayload,
): ReflectionServerState | null {
  if (!current) return current;
  const explorationIndex = current.explorations.findIndex(
    (item) => item.exploration_id === explorationId,
  );
  if (explorationIndex < 0) return current;

  const exploration = current.explorations[explorationIndex];
  const turns = [...exploration.dialogue];
  const userTurn: ReflectionDialogueTurn = {
    turn_id: clientTurnId,
    role: 'user',
    content,
    layer: Math.max(1, exploration.current_layer || 1),
    path: (exploration.selected_path || payload.selected_path) as ReflectionPathId,
    created_at: '',
    client_turn_id: clientTurnId,
  };

  if (!turns.some((turn) => turn.role === 'user' && turn.client_turn_id === clientTurnId)) {
    turns.push(userTurn);
  }
  if (!turns.some((turn) => turn.turn_id === payload.assistant_turn.turn_id)) {
    turns.push(payload.assistant_turn);
  }

  const nextExploration = {
    ...exploration,
    selected_path: payload.selected_path,
    current_layer: payload.current_layer,
    max_layer: Math.max(exploration.max_layer, payload.current_layer),
    status: payload.exploration_status,
    dialogue: turns,
  };

  const explorations = [...current.explorations];
  explorations[explorationIndex] = nextExploration;

  return {
    ...current,
    phase: 'dialogue',
    current_exploration_id: explorationId,
    current_node_id: nextExploration.node_id,
    current_path: payload.selected_path,
    current_layer: payload.current_layer,
    explorations,
    dialogue: turns,
  };
}

function appendPendingDialogueUserTurn(
  current: ReflectionServerState | null,
  explorationId: string,
  content: string,
  clientTurnId: string,
): ReflectionServerState | null {
  if (!current) return current;
  const explorationIndex = current.explorations.findIndex(
    (item) => item.exploration_id === explorationId,
  );
  if (explorationIndex < 0) return current;

  const exploration = current.explorations[explorationIndex];
  if (exploration.dialogue.some((turn) => turn.role === 'user' && turn.client_turn_id === clientTurnId)) {
    return current;
  }

  const userTurn: ReflectionDialogueTurn = {
    turn_id: clientTurnId,
    role: 'user',
    content,
    layer: Math.max(1, exploration.current_layer || current.current_layer || 1),
    path: (exploration.selected_path || current.current_path || '') as ReflectionPathId | '',
    created_at: '',
    client_turn_id: clientTurnId,
  };

  const nextDialogue = [...exploration.dialogue, userTurn];
  const nextExploration = {
    ...exploration,
    dialogue: nextDialogue,
  };
  const explorations = [...current.explorations];
  explorations[explorationIndex] = nextExploration;

  return {
    ...current,
    phase: 'dialogue',
    current_exploration_id: explorationId,
    current_node_id: nextExploration.node_id,
    current_path: (nextExploration.selected_path || current.current_path || null) as ReflectionPathId | null,
    current_layer: nextExploration.current_layer,
    explorations,
    dialogue: nextDialogue,
  };
}

interface ReflectionJourneyCache {
  reflectionState: ReflectionServerState | null;
  viewMode: ReflectionViewMode;
  locallyViewedNodeIds: string[];
}

export function useReflectionJourney(sessionId: string | null, enabled: boolean) {
  const cached = readStageCache<ReflectionJourneyCache>(sessionId, 'reflectionJourney');
  const [reflectionState, setReflectionState] = useState<ReflectionServerState | null>(() => cached?.reflectionState ?? null);
  const [viewMode, setViewMode] = useState<ReflectionViewMode>(() => cached?.viewMode ?? 'landscape');
  const [locallyViewedNodeIds, setLocallyViewedNodeIds] = useState<string[]>(() => cached?.locallyViewedNodeIds ?? []);
  const loadedSessionRef = useRef<string | null>(cached?.reflectionState && sessionId ? sessionId : null);
  const latestViewedRequestRef = useRef(0);

  useEffect(() => {
    if (!sessionId) {
      loadedSessionRef.current = null;
      const timer = window.setTimeout(() => {
        setLocallyViewedNodeIds([]);
        setReflectionState(null);
        setViewMode('landscape');
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    writeStageCache<ReflectionJourneyCache>(sessionId, 'reflectionJourney', {
      reflectionState,
      viewMode,
      locallyViewedNodeIds,
    });
  }, [locallyViewedNodeIds, reflectionState, sessionId, viewMode]);

  useEffect(() => {
    if (!sessionId || !enabled || loadedSessionRef.current === sessionId) {
      return;
    }

    let cancelled = false;

    startReflection(sessionId)
      .then((payload) => {
        if (cancelled) return;
        loadedSessionRef.current = sessionId;
        setReflectionState(payload.state);
        startTransition(() => setViewMode(toViewMode(payload.state)));
      })
      .catch(() => {
        if (cancelled) return;
        loadedSessionRef.current = null;
      });

    return () => {
      cancelled = true;
    };
  }, [enabled, sessionId]);

  async function saveFeelings(node: ReflectionNodeRef, feelings: FeelingId[]) {
    if (!sessionId) return;
    const payload = await markReflectionFeeling(sessionId, { ...node, feelings });
    loadedSessionRef.current = sessionId;
    setReflectionState(payload.state);
    startTransition(() => {
      const nextMode = toViewMode(payload.state);
      setViewMode(nextMode === 'landscape' ? 'explore' : nextMode);
    });
  }

  async function beginDialogue(
    node: ReflectionNodeRef,
    path: ReflectionPathId = 'emotional',
    feelings: FeelingId[] = [],
  ) {
    if (!sessionId) return;
    const payload = await startReflectionDialogue(sessionId, {
      node_id: node.node_id,
      node_type: node.node_type,
      node_label: node.node_label,
      path,
      feelings,
    });
    loadedSessionRef.current = sessionId;
    setReflectionState((current) => mergeDialoguePayload(current, sessionId, node, feelings, payload));
    startTransition(() => setViewMode('dialogue'));
  }

  async function continueDialogue(
    explorationId: string,
    content: string,
    clientTurnId: string,
  ) {
    if (!sessionId) return;
    setReflectionState((current) => appendPendingDialogueUserTurn(current, explorationId, content, clientTurnId));
    startTransition(() => setViewMode('dialogue'));
    const payload = await respondReflectionDialogue(sessionId, {
      exploration_id: explorationId,
      content,
      client_turn_id: clientTurnId,
    });
    loadedSessionRef.current = sessionId;
    setReflectionState((current) => mergeDialogueResponse(current, explorationId, content, clientTurnId, payload));
    startTransition(() => setViewMode('dialogue'));
  }

  async function persistInsight(node: ReflectionNodeRef, insight: string) {
    if (!sessionId) return;
    const payload = await saveReflectionInsight(sessionId, {
      node_id: node.node_id,
      node_type: node.node_type,
      node_label: node.node_label,
      insight,
    });
    loadedSessionRef.current = sessionId;
    setReflectionState(payload.state);
  }

  function markNodeViewed(node: ReflectionNodeRef) {
    setLocallyViewedNodeIds((current) => (
      current.includes(node.node_id) ? current : [...current, node.node_id]
    ));

    if (!sessionId) {
      return;
    }

    const requestId = latestViewedRequestRef.current + 1;
    latestViewedRequestRef.current = requestId;

    markReflectionViewed(sessionId, node)
      .then(() => {
        if (latestViewedRequestRef.current !== requestId) return;
        loadedSessionRef.current = sessionId;
        setReflectionState((current) => mergeViewedNodeState(current, sessionId, node));
      })
      .catch(() => {
        if (latestViewedRequestRef.current !== requestId) return;
        setReflectionState((current) => mergeViewedNodeState(current, sessionId, node));
      });
  }

  return {
    viewedNodeIds: Array.from(new Set([...(reflectionState?.nodes_viewed ?? []), ...locallyViewedNodeIds])),
    beginDialogue,
    continueDialogue,
    persistInsight,
    reflectionState,
    setReflectionState,
    viewMode,
    setViewMode,
    markNodeViewed,
    saveFeelings,
  };
}
