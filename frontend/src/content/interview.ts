export function buildCouncilOpeningMessage(displayName?: string | null): string {
  const name = (displayName ?? '').trim().slice(0, 40);
  return `${name ? `${name}，` : ''}你好。最近在工作、生活、学习或一段关系中有什么处境或决定一直让你反复想着、拿不定主意？`;
}
