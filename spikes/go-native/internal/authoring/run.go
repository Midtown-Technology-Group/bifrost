// Package authoring supplies typed application IO for the explicitly local
// spike. The shared runtime adapter, not this helper, must gate process launch.
package authoring

import (
	"bytes"
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
	provisionBytes, err := bounded(provision)
	if err != nil {
		return err
	}
	ctx, cleanup, err := setup(ctx, bytes.NewReader(provisionBytes))
	if err != nil {
		return err
	}
	defer cleanup()
	inputBytes, err := bounded(input)
	if err != nil {
		return err
	}
	var in Input
	decoder := json.NewDecoder(bytes.NewReader(inputBytes))
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

func bounded(reader io.Reader) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, 65537))
	if err != nil || len(data) > 65536 {
		return nil, errors.New("invalid or oversized authoring input")
	}
	return data, nil
}
