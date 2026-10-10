//go:build linux && amd64

package nativeadapter

import (
	"encoding/json"
	"io"
	"time"

	"github.com/midtown-technology-group/bifrost-go/executionprofile"
)

// PrepareTransport implements the common Offer/Select/Prepare/Prepared stream
// exchange. The guardian supplies accepted bundle references and owns the actual
// pipe endpoints. This function does not authenticate arbitrary io.Readers, read
// private material, launch a child or perform any owner transaction.
// The returned sealed preparation belongs to the caller and must be closed.
func PrepareTransport(parent io.Reader, runtime io.Writer, accepted PreparationInputs, offerID, preparedID string, now func() time.Time) (*PreparedNative, error) {
	if parent == nil || runtime == nil || now == nil || offerID == preparedID {
		return nil, PreparationRejected
	}
	sessionID, ok := accepted.Binding["session_id"].(string)
	if !ok {
		return nil, PreparationRejected
	}
	incarnation, ok := accepted.Binding["runtime_incarnation_id"].(string)
	if !ok {
		return nil, PreparationRejected
	}
	offer := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Offer", "session_id": sessionID, "message_id": offerID, "sequence": 1, "correlation_id": nil,
		"body": map[string]any{"runtime_incarnation_id": incarnation, "supported_protocols": []string{executionprofile.Protocol}, "capabilities": []string{executionprofile.Profile}, "artifact_classes": []string{"native-executable/v1"}}}
	if executionprofile.Write(runtime, offer) != nil {
		return nil, PreparationRejected
	}
	selected, err := executionprofile.Read(parent)
	if err != nil || selected.Frame["type"] != "Select" || selected.Frame["session_id"] != sessionID || selected.Frame["correlation_id"] != offerID {
		return nil, PreparationRejected
	}
	selectedBody := selected.Frame["body"].(map[string]any)
	if selectedBody["protocol"] != executionprofile.Protocol || selectedBody["capability"] != executionprofile.Profile || selectedBody["artifact_class"] != "native-executable/v1" {
		return nil, PreparationRejected
	}
	selectID := selected.Frame["message_id"].(string)
	selectSequence, err := selected.Frame["sequence"].(json.Number).Int64()
	if err != nil || selectID == offerID || selectID == preparedID {
		return nil, PreparationRejected
	}
	prepare, err := executionprofile.Read(parent)
	if err != nil || prepare.Frame["type"] != "Prepare" || prepare.Frame["session_id"] != sessionID || prepare.Frame["correlation_id"] != selectID {
		return nil, PreparationRejected
	}
	prepareID := prepare.Frame["message_id"].(string)
	prepareSequence, err := prepare.Frame["sequence"].(json.Number).Int64()
	if err != nil || prepareSequence <= selectSequence || prepareID == selectID || prepareID == offerID || prepareID == preparedID {
		return nil, PreparationRejected
	}
	prepared, err := PrepareNative(prepare, accepted, now())
	if err != nil {
		return nil, err
	}
	reply := executionprofile.Frame{"protocol": executionprofile.Protocol, "type": "Prepared", "session_id": sessionID, "message_id": preparedID, "sequence": 2, "correlation_id": prepareID, "body": prepared.PreparedBody()}
	if executionprofile.Write(runtime, reply) != nil {
		_ = prepared.Close()
		return nil, PreparationRejected
	}
	return prepared, nil
}
