import { useId, useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
	useUpdateSubscription,
	type EventSubscription,
} from "@/services/events";
import { useWorkflows } from "@/hooks/useWorkflows";
import { InputMappingForm } from "@/components/events/InputMappingForm";
import {
	EventCriteriaForm,
	validateCriteria,
	type EventCriteria,
} from "@/components/events/EventCriteriaForm";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

/**
 * Remove entries where value is undefined, null, or empty string.
 * Returns undefined if no non-empty values remain.
 */
function cleanInputMapping(
	mapping: Record<string, unknown>,
): Record<string, unknown> | undefined {
	const cleaned = Object.fromEntries(
		Object.entries(mapping).filter(
			([, v]) => v !== undefined && v !== null && v !== "",
		),
	);
	return Object.keys(cleaned).length > 0 ? cleaned : undefined;
}

interface EditSubscriptionDialogProps {
	subscription: EventSubscription | null;
	sourceId: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

function EditSubscriptionDialogContent({
	subscription,
	sourceId,
	onOpenChange,
	updateMutation,
}: {
	subscription: EventSubscription;
	updateMutation: ReturnType<typeof useUpdateSubscription>;
	sourceId: string;
	onOpenChange: (open: boolean) => void;
}) {
	const formId = useId();
	const isAgent = Boolean(subscription.agent_id);
	const targetLabel = isAgent ? "Agent" : "Workflow";
	const targetName = isAgent
		? subscription.agent_name || subscription.agent_id
		: subscription.workflow_name || subscription.workflow_id;

	// Fetch available workflows for parameter info
	const { data: workflowsData } = useWorkflows();
	const workflows: WorkflowMetadata[] = workflowsData || [];
	const selectedWorkflow = workflows.find(
		(w) => w.id === subscription.workflow_id,
	);

	// Form state - initialized from props, component remounts when dialog opens
	const [eventType, setEventType] = useState<string>(
		subscription.event_type ?? "",
	);
	const [criteria, setCriteria] = useState<EventCriteria | null>(
		(
			subscription as EventSubscription & {
				criteria?: EventCriteria | null;
			}
		).criteria ?? null,
	);
	const [inputMapping, setInputMapping] = useState<Record<string, unknown>>(
		(subscription.input_mapping as Record<string, unknown>) ?? {},
	);
	const [errors, setErrors] = useState<string[]>([]);

	const isLoading = updateMutation.isPending;

	const validateForm = (): boolean => {
		const newErrors: string[] = [];
		newErrors.push(...validateCriteria(criteria));
		setErrors(newErrors);
		return newErrors.length === 0;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (isLoading || !validateForm()) return;

		try {
			const cleanedMapping = cleanInputMapping(inputMapping);

			await updateMutation.mutateAsync({
				params: {
					path: {
						source_id: sourceId,
						subscription_id: subscription.id,
					},
				},
				body: {
					event_type: eventType.trim() || null,
					criteria,
					input_mapping: cleanedMapping ?? null,
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				} as any,
			});

			toast.success("Subscription updated");
			onOpenChange(false);
		} catch (error) {
			console.error("Failed to update subscription:", error);
			setErrors([
				"Could not save your changes. Your edits are still here. Try again.",
			]);
		}
	};

	return (
		<form
			onSubmit={handleSubmit}
			className="flex min-h-0 min-w-0 max-h-[calc(90dvh-3rem)] flex-col"
		>
			<DialogHeader className="shrink-0">
				<DialogTitle>Edit Subscription</DialogTitle>
				<DialogDescription>
					Update the event type filter and input mapping for this
					subscription.
				</DialogDescription>
			</DialogHeader>

			<div className="min-h-0 min-w-0 flex-1 overflow-y-auto py-4 pr-1">
				<fieldset disabled={isLoading} className="min-w-0 space-y-4">
					{errors.length > 0 && (
						<Alert variant="destructive">
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>
								<ul className="list-disc list-inside">
									{errors.map((error, i) => (
										<li key={i}>{error}</li>
									))}
								</ul>
							</AlertDescription>
						</Alert>
					)}

					{/* Workflow (read-only) */}
					<div className="space-y-2">
						<p className="text-sm font-medium">{targetLabel}</p>
						<div className="text-sm font-medium [overflow-wrap:anywhere]">
							{targetName}
						</div>
						<p className="text-sm leading-6 text-muted-foreground">
							The {targetLabel.toLowerCase()} cannot be changed.
							Create a new subscription to use a different target.
						</p>
					</div>

					{/* Event Type Filter */}
					<div className="space-y-2">
						<Label htmlFor={`${formId}-event-type`}>
							Event Type Filter
						</Label>
						<Input
							id={`${formId}-event-type`}
							className="min-h-11"
							aria-describedby={`${formId}-event-hint`}
							value={eventType}
							onChange={(e) => setEventType(e.target.value)}
							placeholder="e.g., ticket.created"
						/>
						<p
							id={`${formId}-event-hint`}
							className="text-sm leading-6 text-muted-foreground"
						>
							Only trigger this target for events matching this
							type. Leave empty to receive all events.
						</p>
					</div>

                    <div className="space-y-2 border-t pt-4">
                        <Label>Rule Criteria (optional)</Label>
                        <p className="text-xs text-muted-foreground">Only matching events queue this target. Clear criteria to receive all payloads.</p>
                        <EventCriteriaForm value={criteria} onChange={setCriteria} disabled={isLoading} />
                    </div>

					{/* Input Mapping (shown when workflow has parameters) */}
					{selectedWorkflow?.parameters &&
						selectedWorkflow.parameters.length > 0 && (
							<div className="space-y-3">
								<div className="border-t pt-3">
									<Label className="text-sm font-medium">
										Input Mapping (Optional)
									</Label>
									<p className="text-sm leading-6 text-muted-foreground mt-1">
										Map event data to workflow parameters
										using static values or{" "}
										<code className="bg-muted px-1 py-0.5 rounded text-sm">
											{"{{ template }}"}
										</code>{" "}
										expressions.
									</p>
								</div>
								<InputMappingForm
									parameters={selectedWorkflow.parameters}
									values={inputMapping}
									onChange={setInputMapping}
								/>
							</div>
						)}
				</fieldset>
			</div>

			<DialogFooter className="shrink-0 border-t pt-4">
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={isLoading}
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button type="submit" className="min-h-11" disabled={isLoading}>
					{isLoading && (
						<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
					)}
					Save Changes
				</Button>
			</DialogFooter>
		</form>
	);
}

export function EditSubscriptionDialog({
	subscription,
	sourceId,
	open,
	onOpenChange,
}: EditSubscriptionDialogProps) {
	const updateMutation = useUpdateSubscription();
	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!updateMutation.isPending) onOpenChange(nextOpen);
			}}
		>
			<DialogContent className="overflow-hidden sm:max-w-[760px]">
				{open && subscription && (
					<EditSubscriptionDialogContent
						key={subscription.id}
						updateMutation={updateMutation}
						subscription={subscription}
						sourceId={sourceId}
						onOpenChange={onOpenChange}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
