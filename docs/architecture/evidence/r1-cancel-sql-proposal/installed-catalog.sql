-- REVIEW INPUT ONLY. Read-only snapshot on the approved disposable R1 cluster.
-- Invoke psql --no-psqlrc --quiet. Redirect stdout into a caller-owned private
-- 0600 file; never export raw output. A failed/partial snapshot is unqualified.
-- Does not apply/repair schema, privileges, roles, credentials or runtime state.
-- Review all results against the pinned schema/profile BEFORE enabling logins.
\set ON_ERROR_STOP on
\pset format unaligned
\pset tuples_only on
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL search_path = pg_catalog,pg_temp;

SELECT jsonb_build_object(
 'kind','target',
 'database',current_database(), 'session_user',session_user,
 'current_user',current_user, 'server_version_num',current_setting('server_version_num'),
 'heads',(SELECT jsonb_agg(version_num ORDER BY version_num) FROM public.alembic_version)
);

SELECT jsonb_build_object('kind','role','data',to_jsonb(r))
FROM (SELECT oid,rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,
             rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig
      FROM pg_catalog.pg_roles WHERE rolname IN
        ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')) r
ORDER BY r.rolname;

-- Database-wide (setrole=0) and role+database defaults override global role
-- settings; retain every setting on this new task-owned cluster for review.
SELECT jsonb_build_object('kind','database_role_setting','data',to_jsonb(s))
FROM pg_catalog.pg_db_role_setting s ORDER BY s.setdatabase,s.setrole;

-- Parameter grants can confer trigger-disable authority without superuser or
-- membership (notably session_replication_role); reconcile these before LOGIN.
SELECT jsonb_build_object('kind','parameter_acl','data',to_jsonb(a))
FROM pg_catalog.pg_parameter_acl a ORDER BY a.parname;

SELECT jsonb_build_object('kind','effective_parameter_acl','role',r.rolname,
 'parameter',s.name,'context',s.context,'privilege',p.name,
 'allowed',pg_catalog.has_parameter_privilege(r.oid,s.name,p.name))
FROM pg_catalog.pg_settings s CROSS JOIN pg_catalog.pg_roles r
CROSS JOIN (VALUES ('SET'),('ALTER SYSTEM')) p(name)
WHERE r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY s.name,r.rolname,p.name;

SELECT jsonb_build_object('kind','membership','data',to_jsonb(m))
FROM pg_catalog.pg_auth_members m
WHERE m.member IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname IN
 ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard'))
OR m.roleid IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname IN
 ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard'))
ORDER BY m.roleid,m.member;

-- Include other databases as ambient-authority evidence; never infer their ACLs.
SELECT jsonb_build_object('kind','database_acl','role',r.rolname,
 'database',d.datname,'owner',pg_catalog.pg_get_userbyid(d.datdba),'acl',d.datacl,
 'connect',pg_catalog.has_database_privilege(r.oid,d.oid,'CONNECT'),
 'create',pg_catalog.has_database_privilege(r.oid,d.oid,'CREATE'),
 'temporary',pg_catalog.has_database_privilege(r.oid,d.oid,'TEMPORARY'))
FROM pg_catalog.pg_database d CROSS JOIN pg_catalog.pg_roles r
WHERE r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY d.datname,r.rolname;

SELECT jsonb_build_object('kind','schema_acl','role',r.rolname,'schema',n.nspname,
 'owner',pg_catalog.pg_get_userbyid(n.nspowner),'acl',n.nspacl,
 'usage',pg_catalog.has_schema_privilege(r.oid,n.oid,'USAGE'),
 'create',pg_catalog.has_schema_privilege(r.oid,n.oid,'CREATE'))
FROM pg_catalog.pg_namespace n CROSS JOIN pg_catalog.pg_roles r
WHERE n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
AND r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY n.nspname,r.rolname;

SELECT jsonb_build_object('kind','relation','data',to_jsonb(c))
FROM (SELECT c.oid,c.relname,c.relkind,c.relowner,c.relacl,c.relrowsecurity,
             c.relforcerowsecurity,c.relispartition,c.relhassubclass,c.relhasrules
      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public') c ORDER BY c.relname;

SELECT jsonb_build_object('kind','column','relation',c.relname,'name',a.attname,
 'ordinal',a.attnum,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
 'not_null',a.attnotnull,'identity',a.attidentity,'generated',a.attgenerated,
 'collation',a.attcollation,'acl',a.attacl,
 'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid))
FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S')
AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum;

SELECT jsonb_build_object('kind','effective_table_acl','role',r.rolname,
 'relation',c.relname,'privilege',p.name,
 'allowed',pg_catalog.has_table_privilege(r.oid,c.oid,p.name))
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN pg_catalog.pg_roles r
CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) p(name)
WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
AND r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY c.relname,r.rolname,p.name;

SELECT jsonb_build_object('kind','effective_column_acl','role',r.rolname,
 'relation',c.relname,'column',a.attname,'column_acl',a.attacl,'privilege',p.name,
 'allowed',pg_catalog.has_column_privilege(r.oid,c.oid,a.attnum,p.name))
FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace CROSS JOIN pg_catalog.pg_roles r
CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(name)
WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
AND a.attnum>0 AND NOT a.attisdropped
AND r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY c.relname,a.attnum,r.rolname,p.name;

SELECT jsonb_build_object('kind','effective_sequence_acl','role',r.rolname,
 'sequence',c.relname,'privilege',p.name,
 'allowed',pg_catalog.has_sequence_privilege(r.oid,c.oid,p.name))
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN pg_catalog.pg_roles r CROSS JOIN (VALUES ('SELECT'),('UPDATE'),('USAGE')) p(name)
WHERE n.nspname='public' AND c.relkind='S'
AND r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')
ORDER BY c.relname,r.rolname,p.name;

SELECT jsonb_build_object('kind','function','oid',p.oid,'schema',n.nspname,
 'name',p.proname,'arguments',pg_catalog.pg_get_function_identity_arguments(p.oid),
 'owner',pg_catalog.pg_get_userbyid(p.proowner),'acl',p.proacl,'security_definer',p.prosecdef,
 'volatility',p.provolatile,'configuration',p.proconfig,'language',p.prolang,
 'definition',CASE WHEN (n.nspname='public' OR p.prosecdef) AND p.prokind IN ('f','p')
    THEN pg_catalog.pg_get_functiondef(p.oid) ELSE NULL END,
 'effective_execute',(SELECT jsonb_object_agg(r.rolname,
    pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE')) FROM pg_catalog.pg_roles r
    WHERE r.rolname IN ('wex_core','wex_incumbent','runtime_lifecycle_guard','runtime_artifact_lock_guard')))
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
ORDER BY n.nspname,p.proname,p.oid;

SELECT jsonb_build_object('kind','trigger','data',to_jsonb(t),
 'definition',pg_catalog.pg_get_triggerdef(t.oid))
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
ORDER BY c.relname,t.tgname;

SELECT jsonb_build_object('kind','constraint','data',to_jsonb(k),
 'definition',pg_catalog.pg_get_constraintdef(k.oid))
FROM pg_catalog.pg_constraint k WHERE k.connamespace=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='public')
ORDER BY k.conrelid,k.conname;

SELECT jsonb_build_object('kind','index','name',c.relname,'metadata',to_jsonb(i),
 'definition',pg_catalog.pg_get_indexdef(c.oid))
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
WHERE n.nspname='public' AND c.relkind IN ('i','I') ORDER BY c.relname;

SELECT jsonb_build_object('kind','inheritance','data',to_jsonb(i))
FROM pg_catalog.pg_inherits i ORDER BY i.inhrelid,i.inhseqno;

SELECT jsonb_build_object('kind','default_acl','data',to_jsonb(d))
FROM pg_catalog.pg_default_acl d ORDER BY d.defaclrole,d.defaclnamespace,d.defaclobjtype;

SELECT jsonb_build_object('kind','type','data',to_jsonb(t))
FROM (SELECT oid,typname,typowner,typtype,typacl FROM pg_catalog.pg_type
      WHERE typnamespace=(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname='public')) t
ORDER BY t.typname;

SELECT jsonb_build_object('kind','enum','name',t.typname,'label',e.enumlabel,
 'order',e.enumsortorder)
FROM pg_catalog.pg_enum e JOIN pg_catalog.pg_type t ON t.oid=e.enumtypid
JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
WHERE n.nspname='public' ORDER BY t.typname,e.enumsortorder;

SELECT jsonb_build_object('kind','language','name',l.lanname,'owner',l.lanowner,
 'trusted',l.lanpltrusted,'acl',l.lanacl)
FROM pg_catalog.pg_language l ORDER BY l.lanname;

SELECT jsonb_build_object('kind','extension','data',to_jsonb(e))
FROM pg_catalog.pg_extension e ORDER BY e.extname;
COMMIT;
