import { Download } from "lucide-react";
import { useState } from "react";

import { listExecutions } from "@/api/endpoints/workflows";
import type { ExecutionDTO } from "@/api/endpoints/workflows";

interface ExportExecutionsProps {
  workflowId: string;
  workflowName: string;
}

export function ExportExecutions({ workflowId, workflowName }: ExportExecutionsProps) {
  const [isExporting, setIsExporting] = useState(false);

  const exportToCSV = async () => {
    setIsExporting(true);
    try {
      const allExecutions: ExecutionDTO[] = [];
      let cursor: string | undefined = undefined;
      const pageSize = 100;

      // Fetch all executions (cursor-based pagination)
      while (true) {
        const result = await listExecutions(workflowId, {
          cursor,
          limit: pageSize,
        });
        allExecutions.push(...result.items);
        if (!result.has_more || !result.next_cursor) {
          break;
        }
        cursor = result.next_cursor;
      }

      if (allExecutions.length === 0) {
        alert("没有可导出的执行记录");
        return;
      }

      // Build CSV content
      const headers = [
        "执行ID",
        "状态",
        "触发来源",
        "版本号",
        "开始时间",
        "结束时间",
        "持续时间(秒)",
        "错误信息",
      ];

      const rows = allExecutions.map((exec) => {
        const startedAt = exec.started_at ? new Date(exec.started_at) : null;
        const finishedAt = exec.finished_at ? new Date(exec.finished_at) : null;
        const duration =
          startedAt && finishedAt
            ? ((finishedAt.getTime() - startedAt.getTime()) / 1000).toFixed(2)
            : "";

        return [
          exec.id,
          exec.status,
          exec.trigger_source,
          exec.workflow_version_number?.toString() || "",
          startedAt?.toISOString() || "",
          finishedAt?.toISOString() || "",
          duration,
          (exec.error || "").replace(/"/g, '""'), // Escape quotes for CSV
        ];
      });

      const csvContent = [
        headers.join(","),
        ...rows.map((row) => row.map((cell) => `"${cell}"`).join(",")),
      ].join("\n");

      // Add BOM for Excel UTF-8 support
      const bom = "﻿";
      const blob = new Blob([bom + csvContent], {
        type: "text/csv;charset=utf-8;",
      });

      // Trigger download
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const filename = `${workflowName}-executions-${timestamp}.csv`;
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error("导出失败:", error);
      alert("导出失败，请稍后重试");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <button
      type="button"
      onClick={exportToCSV}
      disabled={isExporting}
      className="flex items-center gap-2 rounded-lg border border-line bg-void/50 px-3 py-1.5 text-[11px] text-ice/90 transition hover:border-volt/50 hover:bg-void/70 disabled:opacity-50"
      title="导出执行历史为CSV文件"
    >
      <Download size={12} />
      <span>{isExporting ? "导出中..." : "导出CSV"}</span>
    </button>
  );
}
