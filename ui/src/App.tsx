import { lazy, Suspense, useState } from 'react';
import { Routes, Route, useLocation, Navigate } from 'react-router-dom';
import Sidebar from './components/Sidebar';
import LoginModal from './components/LoginModal';

const Landing = lazy(() => import('./pages/Landing'));
const Chat = lazy(() => import('./pages/Chat'));
const Settings = lazy(() => import('./pages/Settings'));
const SkillsSquare = lazy(() => import('./pages/SkillsSquare'));
const MySkills = lazy(() => import('./pages/MySkills'));
const AdminPage = lazy(() => import('./pages/AdminPage'));

function Loading() {
  return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-secondary)' }}>加载中...</div>;
}

export default function App() {
  const location = useLocation();
  const [loginOpen, setLoginOpen] = useState(false);
  const isLanding = location.pathname === '/';

  return (
    <>
      {!isLanding && <Sidebar onShowLogin={() => setLoginOpen(true)} />}
      <div className={!isLanding ? 'page-with-sidebar' : ''}>
        <Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/skills-square" element={<SkillsSquare />} />
            <Route path="/my-skills" element={<MySkills />} />
            <Route path="/admin" element={<AdminPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </div>
      <LoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
    </>
  );
}
