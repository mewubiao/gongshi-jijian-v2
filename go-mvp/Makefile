.PHONY: build frontend generate test demo
build:
	go build -trimpath -o bin/workshop .
frontend:
	npm ci --no-fund --no-audit
	npm run build
generate:
	go run github.com/sqlc-dev/sqlc/cmd/sqlc@v1.31.1 generate
test:
	go test ./...
	go vet ./...
	go test -race ./...
demo:
	bash scripts/local-demo.sh
