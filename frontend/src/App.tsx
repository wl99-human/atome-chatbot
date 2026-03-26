import { Navigate, Route, Routes } from "react-router-dom";

import { AdminPage } from "./features/admin/AdminPage";
import { CustomerPage } from "./features/customer/CustomerPage";
import { ManagerPage } from "./features/manager/ManagerPage";
import { AppShell } from "./layout/AppShell";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate replace to="/customer" />} />
        <Route path="/customer" element={<CustomerPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/manager" element={<ManagerPage />} />
        <Route path="*" element={<Navigate replace to="/customer" />} />
      </Route>
    </Routes>
  );
}
