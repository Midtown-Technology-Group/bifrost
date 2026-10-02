// Package authoring supplies typed application IO for the explicitly local
// spike. The shared runtime adapter, not this helper, must gate process launch.
package authoring

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
)

type Setup func(context.Context, io.Reader) (context.Context, func(), error)

func Run[Input, Output any](ctx context.Context, args []string, input io.Reader, output io.Writer, provision io.Reader, setup Setup, handler func(context.Context, Input) (Output, error)) error {
	flags := flag.NewFlagSet("workflow", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	local := flags.Bool("local", false, "explicit local spike mode")
	if flags.Parse(args) != nil || !*local || flags.NArg() != 0 {
		return errors.New("only explicit local spike execution is supported")
	}
	ctx, cleanup, err := setup(ctx, io.LimitReader(provision, 65536))
	if err != nil {
		return err
	}
	defer cleanup()
	var in Input
	decoder := json.NewDecoder(io.LimitReader(input, 65536))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&in) != nil {
		return errors.New("invalid workflow input")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return errors.New("trailing workflow input")
	}
	out, err := handler(ctx, in)
	if err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(out)
}
