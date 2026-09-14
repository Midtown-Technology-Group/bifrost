import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { ApplicationSdkUpdateState } from "@/components/applications/ApplicationSdkStatusBadge";
import { webSocketService, type PlatformJobUpdate } from "@/services/websocket";
import type { components } from "@/lib/v1";

type AcceptedSdkUpdate = components["schemas"]["ApplicationSdkUpdateAccepted"];

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

function isSdkUpdateJob(job: PlatformJobUpdate): boolean {
	return job.job_type === "application.sdk_update";
}

function appIdFromJob(job: PlatformJobUpdate): string | null {
	return job.resource_id ?? job.resource_lock_key?.split(":").at(-1) ?? null;
}

function stateFromStatus(status: string): ApplicationSdkUpdateState {
	if (status === "failed" || status === "cancelled") return "failed";
	if (status === "succeeded") return "idle";
	if (status === "queued") return "queued";
	return "updating";
}

export function useApplicationSdkUpdateJobs({
	solutionId,
}: { solutionId?: string | null } = {}) {
	const queryClient = useQueryClient();
	const [states, setStates] = useState<
		Record<string, {
			jobId: string;
			state: ApplicationSdkUpdateState;
			createdAt?: string;
			previousJobIds: string[];
		}>
	>({});

	const invalidateSdkConsumers = useCallback(() => {
		void queryClient.invalidateQueries({
			queryKey: ["get", "/api/applications"],
		});
		void queryClient.invalidateQueries({ queryKey: ["solutions"] });
		if (solutionId) {
			void queryClient.invalidateQueries({
				queryKey: ["solutions", solutionId, "entities"],
			});
			void queryClient.invalidateQueries({
				queryKey: ["solutions", solutionId, "sdk-status"],
			});
		}
	}, [queryClient, solutionId]);

	useEffect(() => {
		return webSocketService.onAnyPlatformJobUpdate((job) => {
			if (!isSdkUpdateJob(job)) return;
			const appId = appIdFromJob(job);
			if (!appId) return;
			const nextState = stateFromStatus(job.status);
			setStates((current) => {
				const previous = current[appId];
				if (previous?.previousJobIds.includes(job.id)) return current;
				if (previous?.createdAt && Date.parse(job.created_at) < Date.parse(previous.createdAt)) {
					return current;
				}
				return {
					...current,
					[appId]: {
						jobId: job.id,
						state: nextState,
						createdAt: job.created_at,
						previousJobIds: previous && previous.jobId !== job.id
							? [...previous.previousJobIds, previous.jobId]
							: previous?.previousJobIds ?? [],
					},
				};
			});
			if (TERMINAL_STATUSES.has(job.status)) {
				invalidateSdkConsumers();
			}
		});
	}, [invalidateSdkConsumers]);

	const trackAccepted = useCallback((accepted: AcceptedSdkUpdate[] = []) => {
		if (!accepted.length) return;
		setStates((current) => {
			const next = { ...current };
			for (const operation of accepted) {
				// A WebSocket update may beat the original enqueue response.
				// Preserve that job's observed progress; a new job can seed state.
				const previous = next[operation.application_id];
				if (previous?.jobId === operation.job_id || previous?.previousJobIds.includes(operation.job_id)) continue;
				next[operation.application_id] = {
					jobId: operation.job_id,
					state: stateFromStatus(operation.status),
					previousJobIds: previous
						? [...previous.previousJobIds, previous.jobId]
						: [],
				};
			}
			return next;
		});
	}, []);

	const getUpdateState = useCallback(
		(appId: string): ApplicationSdkUpdateState => states[appId]?.state ?? "idle",
		[states],
	);

	const isAnyUpdating = useCallback(
		(appIds: string[]): boolean =>
			appIds.some(
				(appId) =>
					states[appId]?.state === "queued" || states[appId]?.state === "updating",
			),
		[states],
	);

	const hasUpdateState = useCallback(
		(appId: string): boolean => appId in states,
		[states],
	);

	return { getUpdateState, hasUpdateState, isAnyUpdating, trackAccepted };
}
