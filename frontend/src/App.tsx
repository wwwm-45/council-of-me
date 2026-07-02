import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import HomeShell from './pages/HomeShell';
import IntroPage from './pages/IntroPage';
import WelcomePage from './pages/WelcomePage';
import ElicitationPage from './pages/ElicitationPage';
import PortraitPage from './pages/portrait/PortraitPage';
import DebatePage from './pages/DebatePage';
import SynthesisPage from './pages/synthesis/SynthesisPage';
import ReflectionPage from './pages/reflection/ReflectionPage';
import ClosurePage from './pages/ClosurePage';
import HistoryPage from './pages/HistoryPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<HomeShell />}>
          <Route path="/" element={<IntroPage />} />
          <Route path="/start" element={<WelcomePage />} />
        </Route>
        <Route element={<Layout />}>
          <Route path="/elicitation" element={<ElicitationPage />} />
          <Route path="/portrait" element={<PortraitPage />} />
          <Route path="/profile" element={<Navigate to="/portrait" replace />} />
          <Route path="/complexity" element={<Navigate to="/portrait" replace />} />
          <Route path="/identity" element={<Navigate to="/portrait" replace />} />
          <Route path="/debate" element={<DebatePage />} />
          <Route path="/synthesis" element={<SynthesisPage />} />
          <Route path="/reflection" element={<ReflectionPage />} />
          <Route path="/closure" element={<ClosurePage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
