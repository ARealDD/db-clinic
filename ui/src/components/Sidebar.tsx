import { NavLink } from 'react-router-dom';
import { useUser } from '../context/UserContext';

interface SidebarProps {
  onShowLogin: () => void;
}

export default function Sidebar({ onShowLogin }: SidebarProps) {
  const { user } = useUser();

  return (
    <>
      <style>{`
        #app-sidebar {
          position: fixed; top: 0; left: 0; width: var(--sidebar-width); height: 100vh;
          background: #141430; border-right: 1px solid var(--border);
          display: flex; flex-direction: column; z-index: 1000;
          color: var(--text-secondary); font-size: 14px; overflow-y: auto;
        }
        .sidebar-logo {
          padding: 20px 16px 12px; font-size: 16px; font-weight: 700;
          color: var(--accent); border-bottom: 1px solid var(--border);
        }
        .sidebar-nav { display: flex; flex-direction: column; padding: 8px; gap: 2px; }
        .nav-item {
          display: flex; align-items: center; gap: 8px; padding: 10px 12px;
          border-radius: 8px; cursor: pointer; text-decoration: none; color: var(--text-secondary);
          transition: background 0.15s, color 0.15s;
        }
        .nav-item:hover { background: #1a1a2e; color: #e0e0e0; }
        .nav-item.active { background: var(--border); color: var(--link); font-weight: 600; }
        .nav-icon { font-size: 16px; width: 20px; text-align: center; }
        .sidebar-spacer { flex: 1; }
        .sidebar-footer {
          display: flex; flex-direction: column; padding: 8px;
          border-top: 1px solid var(--border);
        }
        .nav-user { color: var(--link); cursor: pointer; }
        .nav-user:hover { background: #1a1a2e; }
      `}</style>
      <aside id="app-sidebar">
        <div className="sidebar-logo">DB Clinic</div>
        <nav className="sidebar-nav">
          <NavLink className="nav-item" to="/chat">
            <span className="nav-icon">🤖</span> 诊断
          </NavLink>
          <NavLink className="nav-item" to="/skills-square">
            <span className="nav-icon">📚</span> 技能广场
          </NavLink>
          <NavLink className="nav-item" to="/my-skills">
            <span className="nav-icon">🔧</span> 我的仓库
          </NavLink>
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-footer">
          <NavLink className="nav-item" to="/settings">
            <span className="nav-icon">⚙️</span> 设置
          </NavLink>
          <div className="nav-item nav-user" onClick={onShowLogin}>
            <span className="nav-icon">👤</span>
            <span>{user ? user.username : '未登录'}</span>
          </div>
        </div>
      </aside>
    </>
  );
}
