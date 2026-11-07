package main

import (
	"context"
	"embed"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"workshop/internal/app"
)

//go:embed db/schema.sql web/*.html web/static/*
var assets embed.FS

func main() {
	migrate := flag.Bool("migrate", false, "执行幂等数据库迁移")
	seed := flag.Bool("seed", false, "初始化演示资料")
	demo := flag.Bool("demo", false, "启用明确标注的演示账号")
	once := flag.Bool("once", false, "迁移/种子后退出")
	flag.Parse()
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		log.Fatal("请设置 DATABASE_URL")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()
	if err = pool.Ping(ctx); err != nil {
		log.Fatal(err)
	}
	if *migrate {
		sql, _ := assets.ReadFile("db/schema.sql")
		tx, e := pool.Begin(ctx)
		if e != nil {
			log.Fatal(e)
		}
		if _, e = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(762142311)"); e == nil {
			_, e = tx.Exec(ctx, string(sql))
		}
		if e == nil {
			e = tx.Commit(ctx)
		} else {
			_ = tx.Rollback(ctx)
		}
		if e != nil {
			log.Fatal(e)
		}
	}
	db := app.Database{Pool: pool, Now: time.Now}
	if *seed {
		if !*demo {
			log.Fatal("演示 seed 须同时指定 -demo")
		}
		if err = db.Seed(ctx); err != nil {
			log.Fatal(err)
		}
	}
	if *once {
		return
	}
	handler, err := app.NewServer(db, assets, *demo, os.Getenv("ADMIN_PASSWORD"), os.Getenv("SUPERVISOR_PASSWORD"), os.Getenv("COOKIE_SECURE") == "true")
	if err != nil {
		log.Fatal(err)
	}
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = "127.0.0.1:8080"
	}
	srv := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 30 * time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		<-ctx.Done()
		stop, c := context.WithTimeout(context.Background(), 10*time.Second)
		defer c()
		_ = srv.Shutdown(stop)
	}()
	log.Printf("车间计件 MVP 监听 %s；演示模式=%v", addr, *demo)
	if err = srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
