import SectionShell from "../../components/section-shell";
import UserManagement from "./user-management";

export default function AdminUsersPage() {
  return (
    <SectionShell
      active="/admin/users/"
      eyebrow="WADE QUANT LAB · ACCESS CONTROL"
      title="帳號與權限"
      description="管理平台使用者角色、狀態與交易模式"
    >
      <UserManagement />
    </SectionShell>
  );
}
