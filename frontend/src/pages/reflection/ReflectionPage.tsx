import { Navigate } from 'react-router-dom';
import { getSession, setSession } from '../../store/session';

function targetPathForStatus(status: string) {
  if (status === 'synthesizing' || status === 'reflecting' || status === 'closing') {
    return '/synthesis';
  }
  if (status === 'debating') return '/debate';
  if (status === 'portrait_pending') return '/portrait';
  if (status === 'profile_pending') return '/portrait';
  if (status === 'complexity_pending') return '/portrait';
  if (status === 'identity_pending') return '/portrait';
  if (status === 'identity') return '/identity';
  if (status === 'complexity') return '/complexity';
  if (status === 'profile') return '/profile';
  if (status === 'eliciting') return '/elicitation';
  return '/';
}

export default function ReflectionPage() {
  const session = getSession();
  const target = session.sessionId ? targetPathForStatus(session.status) : '/';

  if (target === '/synthesis' && session.sessionId && session.status === 'synthesizing') {
    setSession({ status: 'reflecting' });
  }

  return <Navigate replace to={target} />;
}
