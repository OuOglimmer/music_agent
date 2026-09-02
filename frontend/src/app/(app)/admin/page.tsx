"use client";

import { useSuspenseQuery } from "@tanstack/react-query"
import { Suspense } from "react"

import { type UserPublic, UsersService } from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import PendingUsers from "@/components/Pending/PendingUsers"
import useAuth from "@/hooks/useAuth"

function getUsersQueryOptions() {
  return {
    queryFn: async () =>
      (await UsersService.readUsers({ query: { skip: 0, limit: 100 } })).data,
    queryKey: ["users"],
  }
}

function UsersTableContent() {
  const { user: currentUser } = useAuth()
  const { data: users } = useSuspenseQuery(getUsersQueryOptions())

  const tableData: UserTableData[] = users.data.map((user: UserPublic) => ({
    ...user,
    isCurrentUser: currentUser?.id === user.id,
  }))

  return <DataTable columns={columns} data={tableData} />
}

function UsersTable() {
  return (
    <Suspense fallback={<PendingUsers />}>
      <UsersTableContent />
    </Suspense>
  )
}

export default function AdminPage() {
  const { user: currentUser } = useAuth()

  if (!currentUser) {
    return null
  }

  if (!currentUser.is_superuser) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="text-center">
          <h1 className="text-xl font-semibold">无权访问</h1>
          <p className="text-muted-foreground">该页面仅管理员可见</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-6 md:p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">用户管理</h1>
          <p className="text-muted-foreground">管理用户账号与权限</p>
        </div>
        <AddUser />
      </div>
      <UsersTable />
    </div>
  )
}
