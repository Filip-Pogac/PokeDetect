import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Navbar } from "./components/Navbar";
import { useAuth } from "./context/AuthContext";
import { CollectionPage } from "./pages/CollectionPage";
import { LoginPage } from "./pages/LoginPage";
import { ScanPage } from "./pages/ScanPage";

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "60vh" }}>
        <span className="spinner spinner-ink" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const { user } = useAuth();

  return (
    <>
      <Navbar />
      <Routes>
        <Route
          path="/login"
          element={user ? <Navigate to="/" replace /> : <LoginPage />}
        />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <ScanPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/collection"
          element={
            <ProtectedRoute>
              <CollectionPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
