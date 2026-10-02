package main

import (
	"context"

	bifrost "github.com/midtown-technology-group/bifrost-go"
	"github.com/midtown-technology-group/bifrost-go/internal/readiness"
)

func Run(ctx context.Context, in readiness.Input) (readiness.Output, error) {
	client, err := bifrost.ClientFromContext(ctx)
	if err != nil {
		return readiness.Output{}, err
	}
	return readiness.Run(ctx, in, client.Integrations)
}

func main() { bifrost.Workflow(Run) }
