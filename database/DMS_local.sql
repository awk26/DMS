-- Local copy of the UAT DMS database (schema + data + 36 stored procedures).
-- Generated from DMS.sql (UAT dump of 2026-11-11); the only change: DEFINER=`jia`@`%` removed,
-- so the procedures belong to whoever runs this script (e.g. root) and work on a local MySQL 8.
-- WARNING: drops and recreates every DMS table in the target server's `DMS` database.

-- MySQL dump 10.13  Distrib 8.0.46, for Linux (x86_64)
--
-- Host: localhost    Database: DMS
-- ------------------------------------------------------
-- Server version	8.0.46-0ubuntu0.24.04.4

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Current Database: `DMS`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `DMS` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `DMS`;

--
-- Table structure for table `access_entries`
--

DROP TABLE IF EXISTS `access_entries`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `access_entries` (
  `id` int NOT NULL AUTO_INCREMENT,
  `target_type` varchar(20) NOT NULL,
  `target_id` int DEFAULT NULL,
  `principal_type` varchar(10) NOT NULL,
  `principal_id` int NOT NULL,
  `permissions` varchar(200) NOT NULL DEFAULT '',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `created_by_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_access_entry` (`target_type`,`target_id`,`principal_type`,`principal_id`),
  KEY `ix_access_target` (`target_type`,`target_id`),
  KEY `ix_access_principal` (`principal_type`,`principal_id`),
  KEY `fk_access_created_by` (`created_by_id`),
  CONSTRAINT `fk_access_created_by` FOREIGN KEY (`created_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=14 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `access_entries`
--

LOCK TABLES `access_entries` WRITE;
/*!40000 ALTER TABLE `access_entries` DISABLE KEYS */;
INSERT INTO `access_entries` VALUES (1,'repository',NULL,'role',1,'view,download,edit,delete,share,manage','2026-11-02 12:07:23.647886',NULL),(2,'repository',NULL,'role',2,'view,download,edit,delete,share,manage','2026-11-02 12:07:23.799924',NULL),(3,'repository',NULL,'role',3,'view,download,edit,share','2026-11-02 12:07:23.877250',NULL),(4,'repository',NULL,'role',4,'view,download','2026-11-02 12:07:24.082661',NULL),(5,'repository',NULL,'role',5,'view','2026-11-02 12:07:24.118756',NULL),(6,'folder',2,'user',2,'view,download,edit,delete,share,manage','2026-11-02 12:20:42.075292',NULL),(7,'folder',3,'user',3,'view,download,edit,delete,share,manage','2026-11-02 12:29:07.610024',NULL),(8,'folder',4,'user',4,'view,download,edit,delete,share,manage','2026-11-03 12:39:40.692156',NULL),(9,'folder',5,'user',5,'view,download,edit,delete,share,manage','2026-11-03 12:42:23.876878',NULL),(10,'folder',7,'user',5,'view,download,edit,delete,share,manage','2026-11-04 11:58:51.274895',5),(11,'document',4,'user',4,'view','2026-11-04 12:00:11.317406',5),(12,'document',4,'user',3,'view','2026-11-04 12:04:29.826386',5),(13,'folder',4,'user',5,'view','2026-11-05 08:48:47.728968',4);
/*!40000 ALTER TABLE `access_entries` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `activity_log`
--

DROP TABLE IF EXISTS `activity_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `activity_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `category` varchar(20) NOT NULL,
  `action` varchar(40) NOT NULL,
  `message` varchar(500) NOT NULL,
  `entity_id` int DEFAULT NULL,
  `user_id` int DEFAULT NULL,
  `user_name` varchar(200) DEFAULT NULL,
  `ip` varchar(64) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `ix_activity_log_user_id` (`user_id`),
  KEY `ix_activity_log_created_at` (`created_at`),
  KEY `ix_activity_cat_action` (`category`,`action`,`created_at`)
) ENGINE=InnoDB AUTO_INCREMENT=97 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `activity_log`
--

LOCK TABLES `activity_log` WRITE;
/*!40000 ALTER TABLE `activity_log` DISABLE KEYS */;
INSERT INTO `activity_log` VALUES (1,'admin','setup','Initial administrator \'awdesh\' created',2,NULL,NULL,'127.0.0.1','2026-11-02 12:20:13.138579'),(2,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:20:41.815460'),(3,'folder','created','Folder \'Personal files\' created for personal files',1,2,'awdesh nonia','127.0.0.1','2026-11-02 12:20:41.957046'),(4,'folder','created','My Files created for \'awdesh\'',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:20:42.081583'),(5,'security','logout','\'awdesh\' signed out',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:28:20.933652'),(6,'security','signup','\'pks\' created an account',3,NULL,NULL,'127.0.0.1','2026-11-02 12:29:07.524065'),(7,'security','login','\'pks\' signed in (password)',3,3,'pawan','127.0.0.1','2026-11-02 12:29:07.563117'),(8,'folder','created','My Files created for \'pks\'',3,3,'pawan','127.0.0.1','2026-11-02 12:29:07.611276'),(9,'security','logout','\'pks\' signed out',3,3,'pawan','127.0.0.1','2026-11-02 12:30:51.546224'),(10,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:31:12.513512'),(11,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:33:35.187056'),(12,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:35:13.136380'),(13,'gmail','connected','Gmail account awdeshnonia709@gmail.com connected',NULL,2,'awdesh nonia','127.0.0.1','2026-11-02 12:35:55.469679'),(14,'email','archived','Archived email \'You’ve sent psannusingh@gmail.com a recovery contact request\'',1,2,'awdesh nonia','127.0.0.1','2026-11-02 12:35:55.611992'),(15,'gmail','baseline','Gmail synchronization baseline established',NULL,2,'awdesh nonia','127.0.0.1','2026-11-02 12:35:55.986102'),(16,'email','archived','Archived email \'test\'',2,NULL,'system',NULL,'2026-11-02 12:44:01.157771'),(17,'email','archived','Archived email \'(no subject)\'',3,NULL,'system',NULL,'2026-11-02 12:44:01.632313'),(18,'email','archived','Archived email \'(no subject)\'',4,NULL,'system',NULL,'2026-11-02 12:44:02.008898'),(19,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-02 12:44:40.232850'),(20,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-03 06:23:50.342765'),(21,'security','login','\'pks\' signed in (password)',3,3,'pawan','127.0.0.1','2026-11-03 09:01:55.973746'),(22,'security','login','\'pks\' signed in (password)',3,3,'pawan','127.0.0.1','2026-11-03 10:02:12.739489'),(23,'security','signup','\'pawan\' created an account',4,NULL,NULL,'127.0.0.1','2026-11-03 12:39:40.610048'),(24,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-03 12:39:40.655754'),(25,'folder','created','My Files created for \'pawan\'',4,4,'pawan singh','127.0.0.1','2026-11-03 12:39:40.715577'),(26,'security','logout','\'pawan\' signed out',4,4,'pawan singh','127.0.0.1','2026-11-03 12:41:40.396700'),(27,'security','signup','\'aws\' created an account',5,NULL,NULL,'127.0.0.1','2026-11-03 12:42:23.809732'),(28,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-03 12:42:23.859403'),(29,'folder','created','My Files created for \'aws\'',5,5,'awdesh','127.0.0.1','2026-11-03 12:42:23.905222'),(30,'security','logout','\'aws\' signed out',5,5,'awdesh','127.0.0.1','2026-11-03 12:51:53.685398'),(31,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-03 12:52:27.174767'),(32,'gmail','connected','Gmail account pawans@jmbaxi.com connected',2,4,'pawan singh','127.0.0.1','2026-11-03 13:02:47.379913'),(33,'gmail','baseline','Gmail synchronization baseline established',NULL,4,'pawan singh','127.0.0.1','2026-11-03 13:02:49.466117'),(34,'email','archived','Archived an email from Gmail account pawans@jmbaxi.com',5,4,'pawan singh','127.0.0.1','2026-11-03 13:04:51.645619'),(35,'email','archived','Archived an email from Gmail account pawans@jmbaxi.com',6,4,'pawan singh','127.0.0.1','2026-11-03 13:04:52.436822'),(36,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 06:01:06.779022'),(37,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 06:04:13.434186'),(38,'security','logout','\'aws\' signed out',5,5,'awdesh','127.0.0.1','2026-11-04 06:04:33.504695'),(39,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 06:04:56.102646'),(40,'email','archived','Archived an email from Gmail account pawans@jmbaxi.com',7,4,'pawan singh','127.0.0.1','2026-11-04 06:06:13.779466'),(41,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 06:12:41.577162'),(42,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 06:40:20.153057'),(43,'security','logout','\'aws\' signed out',5,5,'awdesh','127.0.0.1','2026-11-04 06:43:58.986611'),(44,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 06:44:22.008079'),(45,'gmail','disconnected','Gmail account pawans@jmbaxi.com disconnected',NULL,4,'pawan singh','127.0.0.1','2026-11-04 06:44:43.884482'),(46,'security','logout','\'aws\' signed out',5,5,'awdesh','127.0.0.1','2026-11-04 06:45:27.822534'),(47,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 06:45:50.718902'),(48,'gmail','connected','Gmail account pawans@jmbaxi.com connected',2,4,'pawan singh','127.0.0.1','2026-11-04 06:46:55.334501'),(49,'security','logout','\'pawan\' signed out',4,4,'pawan singh','127.0.0.1','2026-11-04 07:09:53.148251'),(50,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 07:10:20.691633'),(51,'gmail','connected','Gmail account avdeshk@jmbaxi.com connected',3,5,'awdesh','127.0.0.1','2026-11-04 07:11:59.214202'),(52,'gmail','baseline','Gmail synchronization baseline established',NULL,5,'awdesh','127.0.0.1','2026-11-04 07:12:00.504508'),(53,'email','archived','Archived an email from Gmail account avdeshk@jmbaxi.com',8,5,'awdesh','127.0.0.1','2026-11-04 07:12:32.212879'),(54,'gmail','disconnected','Gmail account avdeshk@jmbaxi.com disconnected',NULL,5,'awdesh','127.0.0.1','2026-11-04 07:14:24.499936'),(55,'document','uploaded','Uploaded \'commandS.txt\'',1,5,'awdesh','127.0.0.1','2026-11-04 07:21:17.990609'),(56,'folder','created','Folder \'Personal files / aws / your_folder\' created',6,5,'awdesh','127.0.0.1','2026-11-04 07:22:30.479757'),(57,'document','renamed','Renamed \'commandS.txt\' to \'commandS1.txt\'',1,5,'awdesh','127.0.0.1','2026-11-04 07:25:08.005618'),(58,'document','renamed','Renamed \'commandS1.txt\' to \'commandS.txt\'',1,5,'awdesh','127.0.0.1','2026-11-04 07:25:46.290666'),(59,'document','uploaded','Uploaded \'commandS (1).txt\'',2,5,'awdesh','127.0.0.1','2026-11-04 07:26:18.128467'),(60,'document','uploaded','Uploaded \'commandS (2).txt\'',3,5,'awdesh','127.0.0.1','2026-11-04 07:29:13.856570'),(61,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 11:49:58.691201'),(62,'gmail','disconnected','Gmail account pawans@jmbaxi.com disconnected',NULL,4,'pawan singh','127.0.0.1','2026-11-04 11:50:05.106707'),(63,'gmail','connected','Gmail account pawans@jmbaxi.com connected',2,4,'pawan singh','127.0.0.1','2026-11-04 11:50:30.594265'),(64,'security','logout','\'pawan\' signed out',4,4,'pawan singh','127.0.0.1','2026-11-04 11:50:42.530469'),(65,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 11:51:05.250124'),(66,'security','logout','\'aws\' signed out',5,5,'awdesh','127.0.0.1','2026-11-04 11:51:40.085809'),(67,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 11:51:56.292002'),(68,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 11:52:31.755175'),(69,'gmail','connected','Gmail account avdeshk@jmbaxi.com connected',3,5,'awdesh','127.0.0.1','2026-11-04 11:52:53.757328'),(70,'document','trashed','Moved \'commandS (2).txt\' to the recycle bin',3,5,'awdesh','127.0.0.1','2026-11-04 11:55:28.655845'),(71,'document','trashed','Moved \'commandS (1).txt\' to the recycle bin',2,5,'awdesh','127.0.0.1','2026-11-04 11:55:37.540268'),(72,'document','trashed','Moved \'commandS.txt\' to the recycle bin',1,5,'awdesh','127.0.0.1','2026-11-04 11:55:45.503315'),(73,'folder','created','Folder \'awdesh\' created',7,5,'awdesh','127.0.0.1','2026-11-04 11:58:51.265840'),(74,'document','uploaded','Uploaded \'commandS.txt\'',4,5,'awdesh','127.0.0.1','2026-11-04 11:59:16.189115'),(75,'security','permission_set','Document \'commandS.txt\': pawan singh granted view',4,5,'awdesh','127.0.0.1','2026-11-04 12:00:11.294333'),(76,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-04 12:02:37.403281'),(77,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-04 12:03:25.506129'),(78,'security','permission_set','Document \'commandS.txt\': pawan granted view',4,5,'awdesh','127.0.0.1','2026-11-04 12:04:29.802468'),(79,'email','archived','Archived an email from Gmail account pawans@jmbaxi.com',9,4,'pawan singh','127.0.0.1','2026-11-04 12:07:00.806668'),(80,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-05 06:59:24.294642'),(81,'security','login','\'awdesh\' signed in (password)',2,2,'awdesh nonia','127.0.0.1','2026-11-05 06:59:59.567698'),(82,'security','logout','\'awdesh\' signed out',2,2,'awdesh nonia','127.0.0.1','2026-11-05 07:00:15.823515'),(83,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-05 07:00:45.760774'),(84,'security','permission_set','Document \'commandS.txt\': pawan granted view',4,5,'awdesh','127.0.0.1','2026-11-05 07:01:10.368609'),(85,'security','permission_set','Document \'commandS.txt\': pawan granted view',4,4,'pawan singh','127.0.0.1','2026-11-05 07:02:10.129524'),(86,'security','permission_set','Document \'commandS.txt\': pawan granted view',4,5,'awdesh','127.0.0.1','2026-11-05 07:04:05.599027'),(87,'security','permission_set','Document \'commandS.txt\': pawan singh granted view',4,5,'awdesh','127.0.0.1','2026-11-05 07:04:12.966916'),(88,'security','login_failed','Failed sign-in for \'nobody-smoke-test\'',NULL,NULL,NULL,'127.0.0.1','2026-11-05 08:43:15.302013'),(89,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-05 08:44:54.680902'),(90,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-05 08:45:10.464854'),(91,'document','uploaded','Uploaded \'A Little Reminder_ Our Diwali Pooja Is 2 days away! [1a1a569a61d71d2b].eml\'',5,4,'pawan singh','127.0.0.1','2026-11-05 08:47:19.964244'),(92,'security','permission_set','Folder \'Personal files / pawan\': awdesh granted view',4,4,'pawan singh','127.0.0.1','2026-11-05 08:48:47.208413'),(93,'security','login','\'pawan\' signed in (password)',4,4,'pawan singh','127.0.0.1','2026-11-05 10:40:27.993313'),(94,'security','login','\'aws\' signed in (password)',5,5,'awdesh','127.0.0.1','2026-11-06 05:03:02.963772'),(95,'document','versioned','\'commandS.txt\': uploaded 1.1',4,5,'awdesh','127.0.0.1','2026-11-06 05:04:44.305180'),(96,'document','versioned','\'commandS.txt\': uploaded 1.2',4,5,'awdesh','127.0.0.1','2026-11-06 05:17:41.528396');
/*!40000 ALTER TABLE `activity_log` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `api_tokens`
--

DROP TABLE IF EXISTS `api_tokens`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `api_tokens` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `name` varchar(100) NOT NULL,
  `prefix` varchar(12) NOT NULL,
  `token_hash` varchar(64) NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `last_used_at` datetime(6) DEFAULT NULL,
  `expires_at` datetime(6) DEFAULT NULL,
  `revoked_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_api_tokens_hash` (`token_hash`),
  KEY `ix_api_tokens_user_id` (`user_id`),
  CONSTRAINT `fk_api_tokens_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `api_tokens`
--

LOCK TABLES `api_tokens` WRITE;
/*!40000 ALTER TABLE `api_tokens` DISABLE KEYS */;
/*!40000 ALTER TABLE `api_tokens` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `archived_emails`
--

DROP TABLE IF EXISTS `archived_emails`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `archived_emails` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account_id` int NOT NULL,
  `gmail_message_id` varchar(64) NOT NULL,
  `gmail_thread_id` varchar(64) DEFAULT NULL,
  `message_id_header` varchar(998) DEFAULT NULL,
  `subject` varchar(998) NOT NULL DEFAULT '',
  `sender` varchar(998) NOT NULL DEFAULT '',
  `recipients` text NOT NULL DEFAULT (_utf8mb4''),
  `cc` text NOT NULL DEFAULT (_utf8mb4''),
  `snippet` varchar(500) NOT NULL DEFAULT '',
  `received_at` datetime(6) DEFAULT NULL,
  `imported_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `storage_key` varchar(255) NOT NULL,
  `size` bigint NOT NULL,
  `sha256` varchar(64) NOT NULL,
  `attachment_count` int NOT NULL DEFAULT '0',
  `source` varchar(20) NOT NULL DEFAULT 'sync',
  `gmail_state` varchar(20) NOT NULL DEFAULT 'archived',
  `label_ids` text NOT NULL DEFAULT (_utf8mb4''),
  `state_updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `scan_status` varchar(20) NOT NULL DEFAULT 'unscanned',
  `scan_detail` varchar(300) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_account_message` (`account_id`,`gmail_message_id`),
  UNIQUE KEY `uq_archived_emails_storage_key` (`storage_key`),
  KEY `ix_archived_emails_account_id` (`account_id`),
  KEY `ix_archived_emails_thread` (`gmail_thread_id`),
  KEY `ix_archived_emails_received` (`received_at`),
  KEY `ix_archived_emails_imported_at` (`imported_at`),
  KEY `ix_archived_emails_gmail_state` (`gmail_state`),
  KEY `ix_archived_emails_account_imported` (`account_id`,`imported_at`),
  CONSTRAINT `fk_emails_account` FOREIGN KEY (`account_id`) REFERENCES `gmail_accounts` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `archived_emails`
--

LOCK TABLES `archived_emails` WRITE;
/*!40000 ALTER TABLE `archived_emails` DISABLE KEYS */;
INSERT INTO `archived_emails` VALUES (1,1,'1a153708fc08a28a','1a153708fc08a28a','<9pJ5LhvYi-IDPwa4tchuhg@notifications.google.com>','You’ve sent psannusingh@gmail.com a recovery contact request','Google <no-reply@accounts.google.com>','awdeshnonia709@gmail.com','','[image: Google] You’ve sent psannusingh@gmail.com a recovery contact request awdeshnonia709@gmail.com You’ve asked psannusingh@gmail.com to be your recovery contact, so they can help you sign in if you get locked out of your account. Reach out to them to make sure they respond. This request will exp','2026-10-19 09:07:52.000000','2026-11-02 12:35:55.579435','emails/1/1a153708fc08a28a.eml',13041,'69118a11f0d512b763ecba100e195bc9ad031f4b418708ee983897cbb2be098d',0,'legacy','archived','CATEGORY_UPDATES','2026-11-02 12:35:55.579441','clean',NULL),(2,1,'1a181be5127c8bb8','1a181be5127c8bb8','<CALeeijrCx=_uq6y_UmBt+pmv94oLMgMr9OR1Qb6bLN80h7A16Q@mail.gmail.com>','test','Awdesh Nonia <awdeshnonia@gmail.com>','awdeshnonia709@gmail.com','','testing','2026-10-28 09:01:23.000000','2026-11-02 12:44:01.137706','emails/1/1a181be5127c8bb8.eml',7048,'56a5829e4ebbd7e7b6a4822fd3ff78ff92b75c5f35d5dc84eb8b7731c2fb8cb0',0,'sync','archived','IMPORTANT,CATEGORY_PERSONAL','2026-11-02 12:44:01.137710','clean',NULL),(3,1,'1a19c47f35d8b950','1a19c47f35d8b950','<CAH0BHwaEnC-8KWhHKzr0VyQKw7GD5-emLRPWHSM07o7=MLZeLQ@mail.gmail.com>','','Pawan Lakhan Singh <pawans@jmbaxi.com>','awdeshnonia709@gmail.com','','Hum existing DMS functionality migrate kar rahe hain, isliye schema banate waqt existing functionality ko preserve karna hai. Koi feature accidentally miss nahi hona chahiye. Mera suggestion: Sabse pehle Authentication + Users + Roles + Permissions ka complete database module banate hain. Uske baad ','2026-11-02 12:41:51.000000','2026-11-02 12:44:01.558123','emails/1/1a19c47f35d8b950.eml',10818,'1c870a4fe5145da81d136d2253cb38fcbc85ec346c7ff9885ae7e420f541c498',0,'sync','archived','IMPORTANT,CATEGORY_PERSONAL','2026-11-02 12:44:01.558126','clean',NULL),(4,1,'1a19c48df1f586ab','1a19c48df1f586ab','<CAH0BHwYpiY2AVRORfPr1BQtA9H9aKy=H_f_4zhwUQDhQr=+dGg@mail.gmail.com>','','Pawan Lakhan Singh <pawans@jmbaxi.com>','awdeshnonia709@gmail.com','','currently doing testing -- -- *This organisation is committed in protecting the personal data in compliance as per the regulatory guidelines of the Digital Personal Data Protection Act, 2023 (DPDP Act).We process personal data according to our *Privacy Policy* for specific, legitimate purposes. By c','2026-11-02 12:42:52.000000','2026-11-02 12:44:01.986635','emails/1/1a19c48df1f586ab.eml',9922,'0baa9804662b82bfccedb61b5102e02b865d0c76fbb9829581b5ccea6c77bd0e',0,'sync','archived','IMPORTANT,CATEGORY_PERSONAL','2026-11-02 12:44:01.986638','clean',NULL),(5,2,'1a1a0199ea400ff9','1a1a0199ea400ff9','<b7f445d19286d4b97f37ba66d.dd4f68472d.20261103062908.14b5fbcd1b.b0049af3@mail7.atl91.mcsv.net>','A Little Reminder: Our Diwali Pooja Is 3 days away!','J M BAXI GROUP <corp@jmbaxi.com>','pawans@jmbaxi.com','','https://youtube.com/live/kajVjSJ8ZMc?feature=share https://www.facebook.com/JMBaxiGroup https://www.linkedin.com/company/jmbaxigroup/ https://www.youtube.com/channel/UCG3PnNbcuIhddtaI1KOId6w/videos https://x.com/jmbaxigroup https://www.instagram.com/accounts/login/?next=%2Fjmbaxigroup%2F&source=omni','2026-11-03 06:29:13.000000','2026-11-03 13:04:51.561637','emails/2/1a1a0199ea400ff9.eml',66912,'1119aa9b3c46fbbdaf9be642ed291b655421406d5e4fc567d28cc7bec2d4844d',0,'sync','archived','IMPORTANT,CATEGORY_UPDATES','2026-11-03 13:04:51.561650','clean',NULL),(6,2,'1a1a135336ff0fb7','1a1a135336ff0fb7','<b7f445d19286d4b97f37ba66d.dd4f68472d.20261103113907.d0d1d8fa57.eef015cc@mail180.suw101.mcdlv.net>','Tuticorin Container Terminal Successfully Handles RDO FORTUNE on a Mainline Ad-hoc Call','J M BAXI GROUP <corp@jmbaxi.com>','pawans@jmbaxi.com','','mailto:tuti-sal@jmbaxi.com https://www.facebook.com/JMBaxiGroup/ https://www.youtube.com/@jmbaxigroup https://twitter.com/jmbaxigroup https://www.instagram.com/jmbaxigroup/ https://www.linkedin.com/in/j-m-baxi-group-231614118/ Our mailing address is: TUTICORIN INTERNATIONAL CONTAINER TERMINAL PRIVAT','2026-11-03 11:39:12.000000','2026-11-03 13:04:52.398537','emails/2/1a1a135336ff0fb7.eml',58306,'c6b32284e8a49d1c57d8681cd0858cb3890e26d2a0975ddbce777b33c6b535eb',0,'sync','archived','IMPORTANT,CATEGORY_UPDATES','2026-11-03 13:04:52.398545','clean',NULL),(7,2,'1a1a4daa7a6596a9','1a1a4daa7a6596a9','<b7f445d19286d4b97f37ba66d.dd4f68472d.20261104042945.d44831eb95.66b062bd@mail7.atl91.mcsv.net>','“Data Center Migration” – Know your Application Status','Corporate IT <comp@jmbaxi.com>','pawans@jmbaxi.com','','One-weekend data centre migration: begins Friday 13 Nov 2026, 18:00 hrs – Go-Live Monday 16 Nov 2026, 06:00 hrs. Powering Our Next Chapter Sion C-801Data Centre ONE-WEEKEND MIGRATION NewData Centre Migration Begins Friday, 13 November 2026 18:00 hrs Go-Live Monday, 16 November 2026 06:00 hrs Busines','2026-11-04 04:29:57.000000','2026-11-04 06:06:13.749153','emails/2/1a1a4daa7a6596a9.eml',106169,'b5fb7c629c6fbfd4b6d99f569c2daae164a664c0d7c7b9b2eb15ca59277ceb60',0,'sync','archived','IMPORTANT,CATEGORY_UPDATES','2026-11-04 06:06:13.749158','clean',NULL),(8,3,'1a1a544e9d723ae6','1a1a544e9d723ae6','<494033378.47714.1793774113940@smtp-relay.gmail.com>','Your request has been logged with request id SR-253945 - VPN Access','JMB AskIT <askit@jmbaxi.com>','avdeshk@jmbaxi.com','','Dear Awdesh Kumar, This is an acknowledgement mail for your request.Requester Awdesh Kumar Request ID SR-253945 Location JMBPSSion Status Pending for approval Priority Medium Created Date & Time Nov 4, 2026 12:05 PMSubject VPN AccessDescription Please provide VPN access to the following server IPs f','2026-11-04 06:35:13.000000','2026-11-04 07:12:32.144670','emails/3/1a1a544e9d723ae6.eml',24670,'08d551938349764f42c005f4dc2d164695d824f6b47a410db39ffd4f24279af1',0,'sync','archived','UNREAD,CATEGORY_PERSONAL','2026-11-04 07:12:32.144675','clean',NULL),(9,2,'1a1a569a61d71d2b','1a1a569a61d71d2b','<b7f445d19286d4b97f37ba66d.dd4f68472d.20261104071408.1b5868e68b.9817ac5b@mail7.atl91.mcsv.net>','A Little Reminder: Our Diwali Pooja Is 2 days away!','J M BAXI GROUP <corp@jmbaxi.com>','pawans@jmbaxi.com','','https://youtube.com/live/kajVjSJ8ZMc?feature=share https://www.facebook.com/JMBaxiGroup https://www.linkedin.com/company/jmbaxigroup/ https://www.youtube.com/channel/UCG3PnNbcuIhddtaI1KOId6w/videos https://x.com/jmbaxigroup https://www.instagram.com/accounts/login/?next=%2Fjmbaxigroup%2F&source=omni','2026-11-04 07:14:14.000000','2026-11-04 12:07:00.782052','emails/2/1a1a569a61d71d2b.eml',66911,'e77ea2edd0aa7b9cc8fba0c6668fece9308f088f8433056e749af17e4a8a5896',0,'sync','archived','IMPORTANT,CATEGORY_UPDATES','2026-11-04 12:07:00.782061','clean',NULL);
/*!40000 ALTER TABLE `archived_emails` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `document_links`
--

DROP TABLE IF EXISTS `document_links`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `document_links` (
  `id` int NOT NULL AUTO_INCREMENT,
  `document_id` int NOT NULL,
  `folder_id` int DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_document_link` (`document_id`,`folder_id`),
  KEY `ix_document_links_folder_id` (`folder_id`),
  KEY `fk_links_created_by` (`created_by_id`),
  CONSTRAINT `fk_links_created_by` FOREIGN KEY (`created_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_links_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_links_folder` FOREIGN KEY (`folder_id`) REFERENCES `folders` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `document_links`
--

LOCK TABLES `document_links` WRITE;
/*!40000 ALTER TABLE `document_links` DISABLE KEYS */;
/*!40000 ALTER TABLE `document_links` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `document_tags`
--

DROP TABLE IF EXISTS `document_tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `document_tags` (
  `document_id` int NOT NULL,
  `tag_id` int NOT NULL,
  PRIMARY KEY (`document_id`,`tag_id`),
  KEY `fk_doctags_tag` (`tag_id`),
  CONSTRAINT `fk_doctags_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_doctags_tag` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `document_tags`
--

LOCK TABLES `document_tags` WRITE;
/*!40000 ALTER TABLE `document_tags` DISABLE KEYS */;
/*!40000 ALTER TABLE `document_tags` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `document_types`
--

DROP TABLE IF EXISTS `document_types`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `document_types` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(120) NOT NULL,
  `description` varchar(500) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_document_types_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `document_types`
--

LOCK TABLES `document_types` WRITE;
/*!40000 ALTER TABLE `document_types` DISABLE KEYS */;
/*!40000 ALTER TABLE `document_types` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `document_versions`
--

DROP TABLE IF EXISTS `document_versions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `document_versions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `document_id` int NOT NULL,
  `version_number` int NOT NULL,
  `major` int DEFAULT NULL,
  `minor` int DEFAULT NULL,
  `storage_key` varchar(255) NOT NULL,
  `original_filename` varchar(255) NOT NULL,
  `mime_type` varchar(127) NOT NULL,
  `size` bigint NOT NULL,
  `sha256` varchar(64) NOT NULL,
  `note` varchar(500) DEFAULT NULL,
  `restored_from` int DEFAULT NULL,
  `idempotency_key` varchar(64) DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  `inherit_permissions` tinyint(1) NOT NULL DEFAULT '1',
  `scan_status` varchar(20) NOT NULL DEFAULT 'clean',
  `scan_engine` varchar(40) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_document_version` (`document_id`,`version_number`),
  UNIQUE KEY `uq_versions_storage_key` (`storage_key`),
  UNIQUE KEY `uq_versions_idempotency` (`idempotency_key`),
  KEY `ix_document_versions_document_id` (`document_id`),
  KEY `ix_document_versions_sha256` (`sha256`),
  KEY `fk_versions_created_by` (`created_by_id`),
  CONSTRAINT `fk_versions_created_by` FOREIGN KEY (`created_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_versions_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `document_versions`
--

LOCK TABLES `document_versions` WRITE;
/*!40000 ALTER TABLE `document_versions` DISABLE KEYS */;
INSERT INTO `document_versions` VALUES (1,1,1,1,0,'documents/27/e8/27e86d7716d64adab686789ddba9dd63','commandS.txt','text/plain',196,'917d3129f61e75699afb39798013cd3be578e0d5758e1e71cd4e970f0e6d1d27',NULL,NULL,'194a848c-5a4f-4f68-932e-d55201dd2c1b',5,1,'clean','builtin','2026-11-04 07:21:17.943789'),(2,2,1,1,0,'documents/d8/ea/d8ea55f58a4d42f5b739b8e3bde9d419','commandS.txt','text/plain',196,'917d3129f61e75699afb39798013cd3be578e0d5758e1e71cd4e970f0e6d1d27',NULL,NULL,'fe680f3f-bcb4-43e5-8110-e3cd89425fe7',5,1,'clean','builtin','2026-11-04 07:26:18.087706'),(3,3,1,1,0,'documents/64/60/646083cf60e74325bca6c3fe7efa0815','commandS.txt','text/plain',252,'3ca4f2763110cc5f32d7387a4e6028e2a76d6bee90d186580b20ce150bc3479f',NULL,NULL,'d8374660-8515-4f1d-80e6-1ad72ed46d42',5,1,'clean','builtin','2026-11-04 07:29:13.814572'),(4,4,1,1,0,'documents/c6/eb/c6eb8b7636d147a9b480869bd2921835','commandS.txt','text/plain',252,'3ca4f2763110cc5f32d7387a4e6028e2a76d6bee90d186580b20ce150bc3479f',NULL,NULL,'c0f085fc-784f-4d03-8acf-ef5443a56af5',5,1,'clean','builtin','2026-11-04 11:59:16.058493'),(5,5,1,1,0,'documents/1c/56/1c568a3d83b944848626f3d70065260a','A Little Reminder_ Our Diwali Pooja Is 2 days away! [1a1a569a61d71d2b].eml','message/rfc822',66911,'e77ea2edd0aa7b9cc8fba0c6668fece9308f088f8433056e749af17e4a8a5896',NULL,NULL,'bee4d564-df91-4e95-baf1-5d226136255b',4,1,'clean','builtin','2026-11-05 08:47:20.421231'),(6,4,2,1,1,'documents/2a/6c/2a6cdf96c3544f498fa1951c39a03923','commandS.txt','text/plain',246,'f64d8da18c61db00ae666dd61f3dadfd549652ebfc4972c8dfeb79934c09fa9b',NULL,NULL,'ba895fcb-ea4b-4c51-9a44-2b14aa81dbae',5,1,'clean','builtin','2026-11-06 05:04:45.676116'),(7,4,3,1,2,'documents/91/ba/91baf167b8394c0a86ed4ae417aec370','commandS.txt','text/plain',318,'496c5a516bd5cfa6612b75f4c2869b154bf51249d6cd5b3abe3423d5f4c4f77b',NULL,NULL,'9ee56ee5-24e9-41ed-a850-570e9c221fad',5,1,'clean','builtin','2026-11-06 05:17:42.798307');
/*!40000 ALTER TABLE `document_versions` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `documents`
--

DROP TABLE IF EXISTS `documents`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `documents` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `extension` varchar(16) NOT NULL DEFAULT '',
  `folder_id` int DEFAULT NULL,
  `current_version_id` int DEFAULT NULL,
  `deleted_at` datetime(6) DEFAULT NULL,
  `deleted_batch` varchar(32) DEFAULT NULL,
  `deleted_by_id` int DEFAULT NULL,
  `document_type_id` int DEFAULT NULL,
  `created_by_id` int DEFAULT NULL,
  `owner_id` int DEFAULT NULL,
  `legal_hold` tinyint(1) NOT NULL DEFAULT '0',
  `inherit_permissions` tinyint(1) NOT NULL DEFAULT '1',
  `checked_out_by_id` int DEFAULT NULL,
  `checked_out_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  `row_version` int DEFAULT '1',
  PRIMARY KEY (`id`),
  KEY `ix_documents_folder_id` (`folder_id`),
  KEY `ix_documents_deleted_at` (`deleted_at`),
  KEY `ix_documents_deleted_batch` (`deleted_batch`),
  KEY `ix_documents_updated_at` (`updated_at`),
  KEY `fk_documents_deleted_by` (`deleted_by_id`),
  KEY `fk_documents_doctype` (`document_type_id`),
  KEY `fk_documents_created_by` (`created_by_id`),
  KEY `fk_documents_checked_out` (`checked_out_by_id`),
  KEY `fk_documents_current_version` (`current_version_id`),
  KEY `ix_documents_folder_live` (`folder_id`,`deleted_at`,`name`),
  CONSTRAINT `fk_documents_checked_out` FOREIGN KEY (`checked_out_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_documents_created_by` FOREIGN KEY (`created_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_documents_current_version` FOREIGN KEY (`current_version_id`) REFERENCES `document_versions` (`id`),
  CONSTRAINT `fk_documents_deleted_by` FOREIGN KEY (`deleted_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_documents_doctype` FOREIGN KEY (`document_type_id`) REFERENCES `document_types` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_documents_folder` FOREIGN KEY (`folder_id`) REFERENCES `folders` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `documents`
--

LOCK TABLES `documents` WRITE;
/*!40000 ALTER TABLE `documents` DISABLE KEYS */;
INSERT INTO `documents` VALUES (1,'commandS.txt','txt',5,1,'2026-11-04 11:55:45.503588',NULL,5,NULL,5,1,NULL,NULL,'2026-11-04 07:21:17.930347','2026-11-04 11:55:45.505152',1),(2,'commandS (1).txt','txt',5,2,'2026-11-04 11:55:37.537137',NULL,5,NULL,5,1,NULL,NULL,'2026-11-04 07:26:18.081688','2026-11-04 11:55:37.538795',1),(3,'commandS (2).txt','txt',5,3,'2026-11-04 11:55:28.647216',NULL,5,NULL,5,1,NULL,NULL,'2026-11-04 07:29:13.810210','2026-11-04 11:55:28.651527',1),(4,'commandS.txt','txt',7,7,NULL,NULL,NULL,NULL,5,1,NULL,NULL,'2026-11-04 11:59:16.045850','2026-11-06 05:17:42.803342',5),(5,'A Little Reminder_ Our Diwali Pooja Is 2 days away! [1a1a569a61d71d2b].eml','eml',4,5,NULL,NULL,NULL,NULL,4,1,NULL,NULL,'2026-11-05 08:47:20.396392','2026-11-05 08:47:20.432208',1);
/*!40000 ALTER TABLE `documents` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `email_attachments`
--

DROP TABLE IF EXISTS `email_attachments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `email_attachments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email_id` int NOT NULL,
  `part_index` int NOT NULL,
  `filename` varchar(255) NOT NULL,
  `content_type` varchar(127) NOT NULL,
  `size` bigint NOT NULL,
  `content_id` varchar(255) DEFAULT NULL,
  `is_inline` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `ix_email_attachments_email_id` (`email_id`),
  CONSTRAINT `fk_attach_email` FOREIGN KEY (`email_id`) REFERENCES `archived_emails` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `email_attachments`
--

LOCK TABLES `email_attachments` WRITE;
/*!40000 ALTER TABLE `email_attachments` DISABLE KEYS */;
/*!40000 ALTER TABLE `email_attachments` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `favourites`
--

DROP TABLE IF EXISTS `favourites`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `favourites` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `target_type` varchar(10) NOT NULL,
  `target_id` int NOT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_favourite` (`user_id`,`target_type`,`target_id`),
  KEY `ix_favourites_user_id` (`user_id`),
  CONSTRAINT `fk_fav_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `favourites`
--

LOCK TABLES `favourites` WRITE;
/*!40000 ALTER TABLE `favourites` DISABLE KEYS */;
INSERT INTO `favourites` VALUES (1,5,'document',1,'2026-11-04 07:23:37.652924'),(2,5,'document',4,'2026-11-04 12:09:30.617834');
/*!40000 ALTER TABLE `favourites` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `folder_activity`
--

DROP TABLE IF EXISTS `folder_activity`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `folder_activity` (
  `id` int NOT NULL AUTO_INCREMENT,
  `folder_id` int DEFAULT NULL,
  `folder_name` varchar(255) NOT NULL,
  `action` varchar(40) NOT NULL,
  `details_json` text NOT NULL DEFAULT (_utf8mb4'{}'),
  `user_name` varchar(200) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `ix_folder_activity_folder_id` (`folder_id`),
  KEY `ix_folder_activity_created_at` (`created_at`),
  CONSTRAINT `fk_folder_activity_folder` FOREIGN KEY (`folder_id`) REFERENCES `folders` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `folder_activity`
--

LOCK TABLES `folder_activity` WRITE;
/*!40000 ALTER TABLE `folder_activity` DISABLE KEYS */;
INSERT INTO `folder_activity` VALUES (1,5,'aws','document_added','{\"document\": \"commandS.txt\", \"document_id\": 1, \"via\": \"upload\"}','awdesh','2026-11-04 07:21:17.996014'),(2,6,'your_folder','created','{\"parent\": \"Personal files / aws\"}','awdesh','2026-11-04 07:22:30.483513'),(3,5,'aws','subfolder_added','{\"folder\": \"your_folder\", \"folder_id\": 6}','awdesh','2026-11-04 07:22:30.486188'),(4,5,'aws','document_renamed','{\"document_id\": 1, \"old_name\": \"commandS.txt\", \"new_name\": \"commandS1.txt\"}','awdesh','2026-11-04 07:25:08.006812'),(5,5,'aws','document_renamed','{\"document_id\": 1, \"old_name\": \"commandS1.txt\", \"new_name\": \"commandS.txt\"}','awdesh','2026-11-04 07:25:46.293340'),(6,5,'aws','document_added','{\"document\": \"commandS (1).txt\", \"document_id\": 2, \"via\": \"upload\"}','awdesh','2026-11-04 07:26:18.129325'),(7,5,'aws','document_added','{\"document\": \"commandS (2).txt\", \"document_id\": 3, \"via\": \"upload\"}','awdesh','2026-11-04 07:29:13.859871'),(8,5,'aws','document_trashed','{\"document\": \"commandS (2).txt\", \"document_id\": 3}','awdesh','2026-11-04 11:55:28.680268'),(9,5,'aws','document_trashed','{\"document\": \"commandS (1).txt\", \"document_id\": 2}','awdesh','2026-11-04 11:55:37.563559'),(10,5,'aws','document_trashed','{\"document\": \"commandS.txt\", \"document_id\": 1}','awdesh','2026-11-04 11:55:45.525768'),(11,7,'awdesh','created','{\"parent\": \"Root\"}','awdesh','2026-11-04 11:58:51.288489'),(12,7,'awdesh','document_added','{\"document\": \"commandS.txt\", \"document_id\": 4, \"via\": \"upload\"}','awdesh','2026-11-04 11:59:16.225275'),(13,4,'pawan','document_added','{\"document\": \"A Little Reminder_ Our Diwali Pooja Is 2 days away! [1a1a569a61d71d2b].eml\", \"document_id\": 5, \"via\": \"upload\"}','pawan singh','2026-11-05 08:47:20.513831'),(14,7,'awdesh','document_updated','{\"document\": \"commandS.txt\", \"document_id\": 4, \"version\": \"1.1\", \"restored_from\": null}','awdesh','2026-11-06 05:04:45.751761'),(15,7,'awdesh','document_updated','{\"document\": \"commandS.txt\", \"document_id\": 4, \"version\": \"1.2\", \"restored_from\": null}','awdesh','2026-11-06 05:17:42.984638');
/*!40000 ALTER TABLE `folder_activity` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `folders`
--

DROP TABLE IF EXISTS `folders`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `folders` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `parent_id` int DEFAULT NULL,
  `inherit_permissions` tinyint(1) NOT NULL DEFAULT '1',
  `created_by_id` int DEFAULT NULL,
  `owner_id` int DEFAULT NULL,
  `legal_hold` tinyint(1) NOT NULL DEFAULT '0',
  `deleted_at` datetime(6) DEFAULT NULL,
  `deleted_batch` varchar(32) DEFAULT NULL,
  `deleted_by_id` int DEFAULT NULL,
  `version_keep_last` int DEFAULT NULL,
  `version_keep_years` int DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `ix_folders_parent_id` (`parent_id`),
  KEY `ix_folders_deleted_at` (`deleted_at`),
  KEY `ix_folders_deleted_batch` (`deleted_batch`),
  KEY `fk_folders_created_by` (`created_by_id`),
  KEY `fk_folders_deleted_by` (`deleted_by_id`),
  KEY `ix_folders_parent_live` (`parent_id`,`deleted_at`,`name`),
  CONSTRAINT `fk_folders_created_by` FOREIGN KEY (`created_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_folders_deleted_by` FOREIGN KEY (`deleted_by_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_folders_parent` FOREIGN KEY (`parent_id`) REFERENCES `folders` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=8 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `folders`
--

LOCK TABLES `folders` WRITE;
/*!40000 ALTER TABLE `folders` DISABLE KEYS */;
INSERT INTO `folders` VALUES (1,'Personal files',NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,'2026-11-02 12:20:41.869278','2026-11-02 12:20:41.869288'),(2,'awdesh',1,0,2,NULL,NULL,NULL,NULL,NULL,'2026-11-02 12:20:42.030007','2026-11-02 12:20:42.030012'),(3,'pks',1,0,3,NULL,NULL,NULL,NULL,NULL,'2026-11-02 12:29:07.594566','2026-11-02 12:29:07.594572'),(4,'pawan',1,0,4,NULL,NULL,NULL,NULL,NULL,'2026-11-03 12:39:40.670325','2026-11-03 12:39:40.670335'),(5,'aws',1,0,5,NULL,NULL,NULL,NULL,NULL,'2026-11-03 12:42:23.864626','2026-11-03 12:42:23.864635'),(6,'your_folder',5,1,5,NULL,NULL,NULL,NULL,NULL,'2026-11-04 07:22:30.460312','2026-11-04 07:22:30.460318'),(7,'awdesh',NULL,1,5,NULL,NULL,NULL,NULL,NULL,'2026-11-04 11:58:51.254956','2026-11-04 11:58:51.254970');
/*!40000 ALTER TABLE `folders` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `gmail_accounts`
--

DROP TABLE IF EXISTS `gmail_accounts`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `gmail_accounts` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(320) NOT NULL,
  `history_id` varchar(32) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `connected_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `last_sync_at` datetime(6) DEFAULT NULL,
  `last_success_at` datetime(6) DEFAULT NULL,
  `last_full_sync_at` datetime(6) DEFAULT NULL,
  `last_sync_status` varchar(30) NOT NULL DEFAULT 'never',
  `last_error` varchar(1000) DEFAULT NULL,
  `lock_until` datetime(6) DEFAULT NULL,
  `legacy_imported` tinyint(1) NOT NULL DEFAULT '0',
  `user_id` int DEFAULT NULL,
  `token_enc` text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_gmail_accounts_email` (`email`),
  KEY `ix_gmail_accounts_user_id` (`user_id`),
  CONSTRAINT `fk_gmail_accounts_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `gmail_accounts`
--

LOCK TABLES `gmail_accounts` WRITE;
/*!40000 ALTER TABLE `gmail_accounts` DISABLE KEYS */;
INSERT INTO `gmail_accounts` VALUES (1,'awdeshnonia709@gmail.com','4685',0,'2026-11-02 12:35:55.472303','2026-11-03 09:03:41.943840','2026-11-03 09:03:41.943840',NULL,'ok',NULL,NULL,1,NULL,NULL),(2,'pawans@jmbaxi.com','28707',1,'2026-11-04 11:50:30.593938','2026-11-06 05:16:41.722740','2026-11-06 05:16:41.722740',NULL,'ok',NULL,NULL,1,4,'k1:3s+Ddfyfq2m02rhsbecCjdz7A9FrkUpX4ksBzq8HjqMDzU1m+2SK8P7lN69VAq4NEkE/VFAP/rRZ400O0XgB5yvaIVwHzYgAzKMP9POKi1y8v/KSkPGVyzPq9fkqf5DDwoc9e3gF8Pj0/t1G5RnskMhfFb2z/zOz0IIR219SRMxzTrlcoAW7e22Wng9WI9htNsIlUxRcoM+Uu2ggXZmPSXUBTflIH9stjUy+xNkM3YmsF7NDcGUADdbX+Hz3foKuTYAHDFRZr0eLmS41O08TNu3+EV5Olm01yDssnIClr51KPSwA0/FQPho243bhhe+ogo0Nn+4RsN5TjoH05JVKLk5vukB6FoLeuneBrJEF0M5nxBVvo6EOSp2iToziM5kfbjWxCDhVOjVXmgMd2dDM6a3NEdu6zD33Q1Xe4gMgKA9cSXfX8q8+DCnu+6elEgwKApUCVS02sECBvDXetaFpojU7EpNQ9XgBzt7N8HajSQ+Mw7OTIAfY30VWikGdpEcmw40D51NEm0C4qIbQXnomt0wfWP1fBASVYcYD+55haKy+wc8U47t+nYgkMPg8u+XJrxBNIqTKHkbNW/3JdlwylKXoT+QyomfaqLik5F79XoHUN3c1HovogK8l4oDc4CdHlvgPA1pbXwdjD1xkai2/yq/MEYV+EnI52qyBNIRcMzezVyECPXTiK9vEdXN1oK72DtN/T5lTxLsgSJM0FKyqpeJ135piqrJvsnYw5mS5J+i8oMEzxKCOpsq9lWaJ82xPEx+tvoWIoGtOSVRfwSS1kP4SbAMajClYW6jRYCTPd7m7ZDPvHRtno/dNGDBF3A58Rk5H9tBRvaBr8GM4O43HgcIFa9x8riNWkaYchW9O5p675rBTdUiazhioxj0MQFF8D8VVtvLx2Yx+73Tbd3kLPXMcGT8dtz0v8Yxm2q4NHf5Z8Azr4vKIiiGCcBzyoqGxFKf9HxbEdaQ8LePRcoKYl8zBwMNFgfI1HbF/9tlHXAA1JabOBH+PEjB6NtGuKxipe8X3'),(3,'avdeshk@jmbaxi.com','589762',1,'2026-11-04 11:52:53.757073','2026-11-06 05:16:40.733461','2026-11-06 05:16:40.733461',NULL,'ok',NULL,NULL,1,5,'k1:zgbXM9ZOBW+taV1Vaj3T3mS2gKe2dkMWbuAqphYHK53uVo5XKOhCcQNtQ0VontzDtp36yS2Hr6OCpKP7nTpQc+HFxNpbnfPWM6ZbcFFG6x4tOjHb1WM/y3wJ9JJDvRTuSxTFosBagG3dIJP8rp4aFkXzDJ3qEPJr7iZ1qlb1zbFmLG94GSkle/D57DpDOi7tB7mlqeSxv2eNQxRpr5Pch1+8oEYzH9MP8DcQ57w7gLXOsRZ7XwE/P3/7zxSIGhEbaPn4ggTxPMJqhnAKS6W/ue9sp0p/1kQvPkE8OsACae8PUxeAhImSEd1PGZ+7IUU9q57RGQi8JkDitFVG8/MpYsEQIV1WIY5zfcLlCj+i09qahM4hQOXGhKwLnDJSjWlzLsEXnatP5s2OzDqk10zBQkkpFsujQn7clVIIwZ2DYgfRqyYOMH15tHHSxTZUwE7uUHtlngRNi00EQ02kZzio3Trhzojnd5+39+3Q4NR7PRUlohwAknO0ZIl8YT65BuLCh+M4uThF6V0b2oYJEwwKzMuMKQR8JFNf2skDNjHlhaLOeYMzH36MA7qWKGVgToP5skAG5+comCjUZoKfXN1X5FzAV07DnHp2SQbhXfPaGqycZ3PSHoYqzVxzriE14TYGCTWimu/Dcr3vuHqu+CPakSAKDLgWmlxoLGgWB9NSfB3E584KU4eJD9aGwfnSK+eHJ726ukaOU7C02DDCWUWWYbyPl1TN69yNzPppY7AxMjis7VKFRbsclg7k/1ffhMQ/f21R4KdU9AwTpHgmKgxZ2aF+mJe7FwWXQvGsfBjWW7OuDyXJCvD5nwNlTuy9Mzhlmjlu6xdv6ZkXK5Pd/gtISuWAkyD1K6EG+1bL8OlBWAHwDtdqm3VPSXHCV7E4qzKVshElc37De0U/FHUwFmvnhuyXKEdjWRttdNH2XyudeKdsbte4SAvIBV7MKOYNYEmwVqq/yhszSJ9mgukhwB9ubiTZjo1WCwMZNg61UBANIyXhHHei/E2TCG1iGte2W5TRjboV');
/*!40000 ALTER TABLE `gmail_accounts` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `metadata_fields`
--

DROP TABLE IF EXISTS `metadata_fields`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `metadata_fields` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(60) NOT NULL,
  `label` varchar(120) NOT NULL,
  `field_type` varchar(20) NOT NULL DEFAULT 'text',
  `required` tinyint(1) NOT NULL DEFAULT '0',
  `help_text` varchar(300) NOT NULL DEFAULT '',
  `sort_order` int NOT NULL DEFAULT '0',
  `vocabulary_id` int DEFAULT NULL,
  `document_type_id` int DEFAULT NULL,
  `folder_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `fk_mf_vocab` (`vocabulary_id`),
  KEY `fk_mf_doctype` (`document_type_id`),
  KEY `fk_mf_folder` (`folder_id`),
  CONSTRAINT `fk_mf_doctype` FOREIGN KEY (`document_type_id`) REFERENCES `document_types` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_mf_folder` FOREIGN KEY (`folder_id`) REFERENCES `folders` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_mf_vocab` FOREIGN KEY (`vocabulary_id`) REFERENCES `vocabularies` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `metadata_fields`
--

LOCK TABLES `metadata_fields` WRITE;
/*!40000 ALTER TABLE `metadata_fields` DISABLE KEYS */;
/*!40000 ALTER TABLE `metadata_fields` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `metadata_values`
--

DROP TABLE IF EXISTS `metadata_values`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `metadata_values` (
  `id` int NOT NULL AUTO_INCREMENT,
  `document_id` int NOT NULL,
  `field_id` int NOT NULL,
  `value` text NOT NULL DEFAULT (_utf8mb4''),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_metadata_value` (`document_id`,`field_id`),
  KEY `ix_metadata_values_document_id` (`document_id`),
  KEY `fk_mv_field` (`field_id`),
  CONSTRAINT `fk_mv_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_mv_field` FOREIGN KEY (`field_id`) REFERENCES `metadata_fields` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `metadata_values`
--

LOCK TABLES `metadata_values` WRITE;
/*!40000 ALTER TABLE `metadata_values` DISABLE KEYS */;
/*!40000 ALTER TABLE `metadata_values` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `pending_imports`
--

DROP TABLE IF EXISTS `pending_imports`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `pending_imports` (
  `id` int NOT NULL AUTO_INCREMENT,
  `account_id` int NOT NULL,
  `gmail_message_id` varchar(64) NOT NULL,
  `source` varchar(20) NOT NULL DEFAULT 'sync',
  `status` varchar(20) NOT NULL DEFAULT 'pending',
  `attempts` int NOT NULL DEFAULT '0',
  `last_error` varchar(1000) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_pending_message` (`account_id`,`gmail_message_id`),
  KEY `ix_pending_imports_account_id` (`account_id`),
  CONSTRAINT `fk_pending_account` FOREIGN KEY (`account_id`) REFERENCES `gmail_accounts` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `pending_imports`
--

LOCK TABLES `pending_imports` WRITE;
/*!40000 ALTER TABLE `pending_imports` DISABLE KEYS */;
/*!40000 ALTER TABLE `pending_imports` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `recent_items`
--

DROP TABLE IF EXISTS `recent_items`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `recent_items` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `document_id` int NOT NULL,
  `viewed_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_recent_item` (`user_id`,`document_id`),
  KEY `ix_recent_items_user_id` (`user_id`),
  KEY `ix_recent_items_viewed_at` (`viewed_at`),
  KEY `fk_recent_document` (`document_id`),
  CONSTRAINT `fk_recent_document` FOREIGN KEY (`document_id`) REFERENCES `documents` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_recent_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `recent_items`
--

LOCK TABLES `recent_items` WRITE;
/*!40000 ALTER TABLE `recent_items` DISABLE KEYS */;
INSERT INTO `recent_items` VALUES (1,5,1,'2026-11-04 11:55:40.323228'),(2,5,3,'2026-11-04 11:55:21.698392'),(3,5,2,'2026-11-04 11:55:31.684626'),(4,5,4,'2026-11-06 05:25:24.765207'),(5,4,4,'2026-11-06 05:09:20.092896'),(6,2,4,'2026-11-06 05:09:19.819414'),(7,3,4,'2026-11-06 05:09:19.988400'),(8,2,5,'2026-11-06 05:09:19.894567'),(9,4,5,'2026-11-06 05:09:20.169580'),(10,5,5,'2026-11-06 05:09:20.346180');
/*!40000 ALTER TABLE `recent_items` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `roles`
--

DROP TABLE IF EXISTS `roles`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `roles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(80) NOT NULL,
  `description` varchar(500) NOT NULL DEFAULT '',
  `is_system` tinyint(1) NOT NULL DEFAULT '0',
  `capabilities` varchar(200) NOT NULL DEFAULT '',
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_roles_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `roles`
--

LOCK TABLES `roles` WRITE;
/*!40000 ALTER TABLE `roles` DISABLE KEYS */;
INSERT INTO `roles` VALUES (1,'Admin','Full control of the system, users, roles and settings.',1,'admin,audit,emails,records','2026-11-02 12:07:23.608355'),(2,'Records Manager','Manages the repository, recycle bin, retention and check-outs.',1,'audit,emails,records','2026-11-02 12:07:23.710116'),(3,'Contributor','Uploads and edits documents and creates folders.',1,'','2026-11-02 12:07:23.859578'),(4,'Reader','Views and downloads documents.',1,'','2026-11-02 12:07:24.026565'),(5,'Auditor','Read-only access to content and the audit trail.',1,'audit,emails','2026-11-02 12:07:24.107565');
/*!40000 ALTER TABLE `roles` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `search_entries`
--

DROP TABLE IF EXISTS `search_entries`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `search_entries` (
  `id` int NOT NULL AUTO_INCREMENT,
  `kind` varchar(10) NOT NULL,
  `ref_id` int NOT NULL,
  `title` text NOT NULL DEFAULT (_utf8mb4''),
  `body` mediumtext NOT NULL DEFAULT (_utf8mb4''),
  `meta` text NOT NULL DEFAULT (_utf8mb4''),
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_search_entry` (`kind`,`ref_id`),
  FULLTEXT KEY `ft_search` (`title`,`body`,`meta`) /*!50100 WITH PARSER `ngram` */ 
) ENGINE=InnoDB AUTO_INCREMENT=15 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `search_entries`
--

LOCK TABLES `search_entries` WRITE;
/*!40000 ALTER TABLE `search_entries` DISABLE KEYS */;
INSERT INTO `search_entries` VALUES (1,'email',1,'You’ve sent psannusingh@gmail.com a recovery contact request','You’ve sent psannusingh@gmail.com a recovery contact request\nGoogle <no-reply@accounts.google.com>\nawdeshnonia709@gmail.com\n[image: Google]\r\nYou’ve sent psannusingh@gmail.com a recovery contact request\r\n\r\n\r\nawdeshnonia709@gmail.com\r\n\r\nYou’ve asked psannusingh@gmail.com to be your recovery contact, so they can\r\nhelp you sign in if you get locked out of your account.\r\n\r\nReach out to them to make sure they respond. This request will expire in 7\r\ndays or you can cancel it at any time.\r\n\r\nIf you didn’t make this request, go to your account settings and cancel the\r\nrequest now.\r\nGo to settings\r\n<https://accounts.google.com/AccountChooser?Email=awdeshnonia709@gmail.com&continue=https://myaccount.google.com/signinoptions/recoverycontacts>\r\nYou can also see security activity at\r\nhttps://myaccount.google.com/notifications\r\nYou received this email to let you know about important changes to your\r\nGoogle Account and services.\r\n© 2026 Google LLC, 1600 Amphitheatre Parkway, Mountain View, CA 94043, USA\r\n','Google <no-reply@accounts.google.com> \nawdeshnonia709@gmail.com \n \n \narchived','2026-11-02 12:35:55.597651'),(2,'email',2,'test','test\nAwdesh Nonia <awdeshnonia@gmail.com>\nawdeshnonia709@gmail.com\ntesting\r\n','Awdesh Nonia <awdeshnonia@gmail.com> \nawdeshnonia709@gmail.com \n \n \narchived','2026-11-02 12:44:01.196131'),(3,'email',3,'','Pawan Lakhan Singh <pawans@jmbaxi.com>\nawdeshnonia709@gmail.com\nHum existing DMS functionality migrate kar rahe hain, isliye schema banate\r\nwaqt existing functionality ko preserve karna hai. Koi feature accidentally\r\nmiss nahi hona chahiye.\r\n\r\nMera suggestion: Sabse pehle Authentication + Users + Roles + Permissions\r\nka complete database module banate hain. Uske baad Folders aur Documents\r\npar jayenge.\r\n\r\nIs tarah har module independently test bhi kar sakenge aur database ka\r\nstructure maintainable rahega.\r\n\r\n-- \r\n\r\n\r\n-- \r\n\r\n\r\n*This organisation is committed in protecting the personal data in \r\ncompliance as per the regulatory guidelines of the Digital Personal Data \r\nProtection Act, 2023 (DPDP Act).We process personal data according to our \r\n*Privacy Policy* for specific, legitimate purposes. By communicating with \r\nus, you acknowledge our data handling practices.*\r\n\r\n*The Information \r\ncontained in this transmission may be attorney/client privileged and \r\ntherefore confidential. This information is intended solely for the use of \r\nthe individual or entity to whom they are addressed. If the reader of this \r\nmessage is not the intended recipient, you are hereby notified that any \r\ndissemination, distribution, printing or copy of the communication is \r\nstrictly prohibited. If you receive this transmission in error, or if you \r\nare not the individual or entity named above, the receipt of this \r\ntransmission is not intended to and does not waive any privilege, \r\nattorney/client or otherwise. If you are not the intended recipient, any \r\nunauthorized use of this email\'s contents is strictly prohibited and you \r\nare required to please notify the sender immediately and delete the \r\nmessage*. \r\n','Pawan Lakhan Singh <pawans@jmbaxi.com> \nawdeshnonia709@gmail.com \n \n \narchived','2026-11-02 12:44:01.638881'),(4,'email',4,'','Pawan Lakhan Singh <pawans@jmbaxi.com>\nawdeshnonia709@gmail.com\ncurrently doing testing\r\n\r\n-- \r\n\r\n\r\n-- \r\n\r\n\r\n*This organisation is committed in protecting the personal data in \r\ncompliance as per the regulatory guidelines of the Digital Personal Data \r\nProtection Act, 2023 (DPDP Act).We process personal data according to our \r\n*Privacy Policy* for specific, legitimate purposes. By communicating with \r\nus, you acknowledge our data handling practices.*\r\n\r\n*The Information \r\ncontained in this transmission may be attorney/client privileged and \r\ntherefore confidential. This information is intended solely for the use of \r\nthe individual or entity to whom they are addressed. If the reader of this \r\nmessage is not the intended recipient, you are hereby notified that any \r\ndissemination, distribution, printing or copy of the communication is \r\nstrictly prohibited. If you receive this transmission in error, or if you \r\nare not the individual or entity named above, the receipt of this \r\ntransmission is not intended to and does not waive any privilege, \r\nattorney/client or otherwise. If you are not the intended recipient, any \r\nunauthorized use of this email\'s contents is strictly prohibited and you \r\nare required to please notify the sender immediately and delete the \r\nmessage*. \r\n','Pawan Lakhan Singh <pawans@jmbaxi.com> \nawdeshnonia709@gmail.com \n \n \narchived','2026-11-02 12:44:02.010902'),(5,'email',5,'A Little Reminder: Our Diwali Pooja Is 3 days away!','A Little Reminder: Our Diwali Pooja Is 3 days away!\nJ M BAXI GROUP <corp@jmbaxi.com>\npawans@jmbaxi.com\nhttps://youtube.com/live/kajVjSJ8ZMc?feature=share\r\nhttps://www.facebook.com/JMBaxiGroup\r\nhttps://www.linkedin.com/company/jmbaxigroup/\r\nhttps://www.youtube.com/channel/UCG3PnNbcuIhddtaI1KOId6w/videos\r\nhttps://x.com/jmbaxigroup\r\nhttps://www.instagram.com/accounts/login/?next=%2Fjmbaxigroup%2F&source=omni_redirect\r\n\r\nOur mailing address is:\r\n\r\n1001 - A & B wing, Godrej Coliseum, Everard Nagar, Sion East, Mumbai 400 022.\r\nT: +91 22 6153 7900 | E: corp@jmbaxi.com (mailto:corp@jmbaxi.com?subject=&body=) | W: www.jmbaxi.com\r\n\r\nThis email was sent to pawans@jmbaxi.com (mailto:pawans@jmbaxi.com)\r\nwhy did I get this? (https://jmbaxi.us17.list-manage.com/about?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=14b5fbcd1b)     unsubscribe from this list (https://jmbaxi.us17.list-manage.com/unsubscribe?u=b7f445d19286d4b97f37ba66d&id=60effb2745&t=b&e=dd4f68472d&c=14b5fbcd1b)     update subscription preferences (https://jmbaxi.us17.list-manage.com/profile?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=14b5fbcd1b)\r\nJ M Baxi Group . 1001 - \'A & B\' wing, Godrej Coliseum, . Everard Nagar, Sion (E) . Mumbai, Maharashtra 400022 . India','J M BAXI GROUP <corp@jmbaxi.com> \npawans@jmbaxi.com \n \n \narchived','2026-11-03 13:04:51.666823'),(6,'email',6,'Tuticorin Container Terminal Successfully Handles RDO FORTUNE on a Mainline Ad-hoc Call','Tuticorin Container Terminal Successfully Handles RDO FORTUNE on a Mainline Ad-hoc Call\nJ M BAXI GROUP <corp@jmbaxi.com>\npawans@jmbaxi.com\nmailto:tuti-sal@jmbaxi.com\r\nhttps://www.facebook.com/JMBaxiGroup/\r\nhttps://www.youtube.com/@jmbaxigroup\r\nhttps://twitter.com/jmbaxigroup\r\nhttps://www.instagram.com/jmbaxigroup/\r\nhttps://www.linkedin.com/in/j-m-baxi-group-231614118/\r\n\r\nOur mailing address is:\r\nTUTICORIN INTERNATIONAL CONTAINER TERMINAL PRIVATE LIMITED\r\nB6 World Trade Avenue, Harbour Estate, Tuticorin 628004 Tamil Nadu, India\r\nW: https://jmbaxi.com\r\n\r\n============================================================\r\n\r\nThis email was sent to pawans@jmbaxi.com (mailto:pawans@jmbaxi.com)\r\nwhy did I get this? (https://jmbaxi.us17.list-manage.com/about?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=d0d1d8fa57)     unsubscribe from this list (https://jmbaxi.us17.list-manage.com/unsubscribe?u=b7f445d19286d4b97f37ba66d&id=60effb2745&t=b&e=dd4f68472d&c=d0d1d8fa57)     update subscription preferences (https://jmbaxi.us17.list-manage.com/profile?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=d0d1d8fa57)\r\nJ M Baxi Group . 1001 - \'A & B\' wing, Godrej Coliseum, . Everard Nagar, Sion (E) . Mumbai, Maharashtra 400022 . India','J M BAXI GROUP <corp@jmbaxi.com> \npawans@jmbaxi.com \n \n \narchived','2026-11-03 13:04:52.462036'),(7,'email',7,'“Data Center Migration” – Know your Application Status','“Data Center Migration” – Know your Application Status\nCorporate IT <comp@jmbaxi.com>\npawans@jmbaxi.com\nOne-weekend data centre migration: begins Friday 13 Nov 2026, 18:00 hrs – Go-Live Monday 16 Nov 2026, 06:00 hrs.\r\n\r\nPowering Our Next Chapter   Sion C-801Data Centre ONE-WEEKEND MIGRATION NewData Centre   Migration Begins Friday, 13 November 2026 18:00 hrs Go-Live Monday, 16 November 2026 06:00 hrs Business Keeps Moving -Disaster Recovery (DR) TOS Running as Usual Email ZohoCRM   Diabos3.0 TOS(MCCT) CLMS Legatrix GenSuite   Taking a Short Break AgencyVerticals eBMS Nexera SML ERP ABC FAS Lineup   CommonApplications DarwinBox AskIT Work Permit LMS   JMBPL TOS (BPPL) EAM   Your Data. Our Journey. A New Destination.   SECURE SEAMLESS FUTURE-READY (https://mcusercontent.com/b7f445d19286d4b97f37ba66d/files/86a6574b-f2a7-c406-8a21-c416b532b4b2/powering_next_chapter.02.pdf)\r\n\r\nThis email was sent to pawans@jmbaxi.com (mailto:pawans@jmbaxi.com)\r\nwhy did I get this? (https://jmbaxi.us17.list-manage.com/about?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=d44831eb95)     unsubscribe from this list (https://jmbaxi.us17.list-manage.com/unsubscribe?u=b7f445d19286d4b97f37ba66d&id=60effb2745&t=b&e=dd4f68472d&c=d44831eb95)     update subscription preferences (https://jmbaxi.us17.list-manage.com/profile?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=d44831eb95)\r\nJ M Baxi Group . 1001 - \'A & B\' wing, Godrej Coliseum, . Everard Nagar, Sion (E) . Mumbai, Maharashtra 400022 . India','Corporate IT <comp@jmbaxi.com> \npawans@jmbaxi.com \n \n \narchived','2026-11-04 06:06:13.778170'),(8,'email',8,'Your request has been logged with request id SR-253945 - VPN Access','Your request has been logged with request id SR-253945 - VPN Access\nJMB AskIT <askit@jmbaxi.com>\navdeshk@jmbaxi.com\nDear Awdesh Kumar,                This is an acknowledgement mail for your request.Requester               Awdesh Kumar          Request ID               SR-253945                  Location               JMBPSSion                  Status               Pending for approval                       Priority               Medium      Created Date & Time                     Nov 4, 2026 12:05 PMSubject                     VPN AccessDescription                     Please provide VPN access to the following server IPs for my employee ID jmbaxigrp\\3900127.My previous Sophos username was avadeshk. Kindly update the access accordingly and enable VPN connectivity for the required servers.The status of the request can be tracked at https://askit.jmbaxi.com/app/itdesk/ui/requests/14940000057100587/details            Please get back to us for any further clarifications.          Regards,          JMB      AskIT     This is an automated message. Please do not reply to this email.      \n\n\n\nThis organisation is committed in protecting the personal data in compliance as per the regulatory guidelines of the Digital Personal Data Protection Act, 2023 (DPDP Act).We process personal data according to our Privacy Policy for specific, legitimate purposes. By communicating with us, you acknowledge our data handling practices.The Information contained in this transmission may be attorney/client privileged and therefore confidential. This information is intended solely for the use of the individual or entity to whom they are addressed. If the reader of this message is not the intended recipient, you are hereby notified that any dissemination, distribution, printing or copy of the communication is strictly prohibited. If you receive this transmission in error, or if you are not the individual or entity named above, the receipt of this transmission is not intended to and does not waive any privilege, attorney/client or otherwise. If you are not the intended recipient, any unauthorized use of this email\'s contents is strictly prohibited and you are required to please notify the sender immediately and delete the message. \n','JMB AskIT <askit@jmbaxi.com> \navdeshk@jmbaxi.com \n \n \narchived','2026-11-04 07:12:32.175351'),(12,'document',4,'commandS.txt','to make the virtual environment for the project: python -m venv venv \ncommand to activate the virtual environment: .\\venv\\Scripts\\Activate.ps1 \ncommand to run flutter device: flutter run -d emulator-5554 \n\ntrying to test the versioning \n\nthis is to test...................................................','txt \nText \nawdesh \nawdesh','2026-11-06 05:17:43.007512'),(13,'email',9,'A Little Reminder: Our Diwali Pooja Is 2 days away!','A Little Reminder: Our Diwali Pooja Is 2 days away!\nJ M BAXI GROUP <corp@jmbaxi.com>\npawans@jmbaxi.com\nhttps://youtube.com/live/kajVjSJ8ZMc?feature=share\r\nhttps://www.facebook.com/JMBaxiGroup\r\nhttps://www.linkedin.com/company/jmbaxigroup/\r\nhttps://www.youtube.com/channel/UCG3PnNbcuIhddtaI1KOId6w/videos\r\nhttps://x.com/jmbaxigroup\r\nhttps://www.instagram.com/accounts/login/?next=%2Fjmbaxigroup%2F&source=omni_redirect\r\n\r\nOur mailing address is:\r\n\r\n1001 - A & B wing, Godrej Coliseum, Everard Nagar, Sion East, Mumbai 400 022.\r\nT: +91 22 6153 7900 | E: corp@jmbaxi.com (mailto:corp@jmbaxi.com?subject=&body=) | W: www.jmbaxi.com\r\n\r\nThis email was sent to pawans@jmbaxi.com (mailto:pawans@jmbaxi.com)\r\nwhy did I get this? (https://jmbaxi.us17.list-manage.com/about?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=1b5868e68b)     unsubscribe from this list (https://jmbaxi.us17.list-manage.com/unsubscribe?u=b7f445d19286d4b97f37ba66d&id=60effb2745&t=b&e=dd4f68472d&c=1b5868e68b)     update subscription preferences (https://jmbaxi.us17.list-manage.com/profile?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=1b5868e68b)\r\nJ M Baxi Group . 1001 - \'A & B\' wing, Godrej Coliseum, . Everard Nagar, Sion (E) . Mumbai, Maharashtra 400022 . India','J M BAXI GROUP <corp@jmbaxi.com> \npawans@jmbaxi.com \n \n \narchived','2026-11-04 12:07:00.805661'),(14,'document',5,'A Little Reminder_ Our Diwali Pooja Is 2 days away! [1a1a569a61d71d2b].eml','A Little Reminder: Our Diwali Pooja Is 2 days away!\nJ M BAXI GROUP <corp@jmbaxi.com>\npawans@jmbaxi.com\nhttps://youtube.com/live/kajVjSJ8ZMc?feature=share \nhttps://www.facebook.com/JMBaxiGroup \nhttps://www.linkedin.com/company/jmbaxigroup/ \nhttps://www.youtube.com/channel/UCG3PnNbcuIhddtaI1KOId6w/videos \nhttps://x.com/jmbaxigroup \nhttps://www.instagram.com/accounts/login/?next=%2Fjmbaxigroup%2F&source=omni_redirect \n\nOur mailing address is: \n\n1001 - A & B wing, Godrej Coliseum, Everard Nagar, Sion East, Mumbai 400 022. \nT: +91 22 6153 7900 | E: corp@jmbaxi.com (mailto:corp@jmbaxi.com?subject=&body=) | W: www.jmbaxi.com \n\nThis email was sent to pawans@jmbaxi.com (mailto:pawans@jmbaxi.com) \nwhy did I get this? (https://jmbaxi.us17.list-manage.com/about?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=1b5868e68b) unsubscribe from this list (https://jmbaxi.us17.list-manage.com/unsubscribe?u=b7f445d19286d4b97f37ba66d&id=60effb2745&t=b&e=dd4f68472d&c=1b5868e68b) update subscription preferences (https://jmbaxi.us17.list-manage.com/profile?u=b7f445d19286d4b97f37ba66d&id=60effb2745&e=dd4f68472d&c=1b5868e68b) \nJ M Baxi Group . 1001 - \'A & B\' wing, Godrej Coliseum, . Everard Nagar, Sion (E) . Mumbai, Maharashtra 400022 . India','eml \nEmail \nPersonal files / pawan \npawan singh','2026-11-05 08:47:20.525543');
/*!40000 ALTER TABLE `search_entries` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `system_settings`
--

DROP TABLE IF EXISTS `system_settings`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `system_settings` (
  `key` varchar(80) NOT NULL,
  `value_json` text NOT NULL,
  `updated_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `system_settings`
--

LOCK TABLES `system_settings` WRITE;
/*!40000 ALTER TABLE `system_settings` DISABLE KEYS */;
INSERT INTO `system_settings` VALUES ('home_container_folder_id','1','2026-11-02 12:20:41.949516');
/*!40000 ALTER TABLE `system_settings` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `tags`
--

DROP TABLE IF EXISTS `tags`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tags` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(80) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_tags_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tags`
--

LOCK TABLES `tags` WRITE;
/*!40000 ALTER TABLE `tags` DISABLE KEYS */;
/*!40000 ALTER TABLE `tags` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `user_devices`
--

DROP TABLE IF EXISTS `user_devices`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_devices` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `device_hash` varchar(64) NOT NULL,
  `platform` varchar(30) NOT NULL DEFAULT 'other',
  `user_agent` varchar(300) NOT NULL DEFAULT '',
  `status` varchar(20) NOT NULL DEFAULT 'approved',
  `first_seen` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `last_seen` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `last_ip` varchar(64) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_user_device` (`user_id`,`device_hash`),
  KEY `ix_user_devices_user_id` (`user_id`),
  CONSTRAINT `fk_user_devices_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=13 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `user_devices`
--

LOCK TABLES `user_devices` WRITE;
/*!40000 ALTER TABLE `user_devices` DISABLE KEYS */;
INSERT INTO `user_devices` VALUES (1,2,'4439483837f7aaf67176b195bc6cb847345e5530b98cc47d15e795c0e6c521e3','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-02 12:20:41.718547','2026-11-03 06:23:50.342807','127.0.0.1'),(2,3,'4439483837f7aaf67176b195bc6cb847345e5530b98cc47d15e795c0e6c521e3','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-02 12:29:07.546280','2026-11-03 10:02:12.751188','127.0.0.1'),(3,2,'2d0726fb74b99f1a2a67f603505640896fabd0495b84ce54d1a81382fcc547b0','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0','approved','2026-11-02 12:33:35.170187','2026-11-05 07:00:00.113397','127.0.0.1'),(4,2,'3f6b255c5967d760ab424ac9b759e0810c42f147e436236d62f3dda66135e9d9','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-02 12:35:13.120420','2026-11-02 12:35:13.124383','127.0.0.1'),(5,4,'4439483837f7aaf67176b195bc6cb847345e5530b98cc47d15e795c0e6c521e3','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-03 12:39:40.613832','2026-11-05 10:40:28.556682','127.0.0.1'),(6,5,'4439483837f7aaf67176b195bc6cb847345e5530b98cc47d15e795c0e6c521e3','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-03 12:42:23.809681','2026-11-04 11:51:05.257772','127.0.0.1'),(7,5,'22731aee0e7acb1ab89749fbfabbaa8e79c1fef9d0d0dad886cb9a0b9b98298a','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0','approved','2026-11-04 06:01:06.775022','2026-11-04 06:01:06.782587','127.0.0.1'),(8,5,'7c488a4284b2cace372455da6c8e31f12b531a3859008faacafb9816d17b760e','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-04 06:12:41.571971','2026-11-04 11:52:31.764512','127.0.0.1'),(9,4,'7c488a4284b2cace372455da6c8e31f12b531a3859008faacafb9816d17b760e','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-04 06:44:22.001171','2026-11-04 06:44:22.004024','127.0.0.1'),(10,5,'57e8db9d1eb2b2c82c90850f012eae79a81447cc17c177faa2e8dd0e0b74b103','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36','approved','2026-11-04 12:02:37.404732','2026-11-04 12:02:37.410791','127.0.0.1'),(11,4,'2d0726fb74b99f1a2a67f603505640896fabd0495b84ce54d1a81382fcc547b0','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0','approved','2026-11-04 12:03:25.506995','2026-11-04 12:03:25.518857','127.0.0.1'),(12,5,'2d0726fb74b99f1a2a67f603505640896fabd0495b84ce54d1a81382fcc547b0','windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:159.0) Gecko/20100101 Firefox/159.0','approved','2026-11-05 07:00:46.303720','2026-11-06 05:03:04.320955','127.0.0.1');
/*!40000 ALTER TABLE `user_devices` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `user_roles`
--

DROP TABLE IF EXISTS `user_roles`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_roles` (
  `user_id` int NOT NULL,
  `role_id` int NOT NULL,
  PRIMARY KEY (`user_id`,`role_id`),
  KEY `fk_user_roles_role` (`role_id`),
  CONSTRAINT `fk_user_roles_role` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_user_roles_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `user_roles`
--

LOCK TABLES `user_roles` WRITE;
/*!40000 ALTER TABLE `user_roles` DISABLE KEYS */;
INSERT INTO `user_roles` VALUES (2,1),(3,3),(4,3),(5,3);
/*!40000 ALTER TABLE `user_roles` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `username` varchar(150) NOT NULL,
  `email` varchar(320) DEFAULT NULL,
  `display_name` varchar(200) NOT NULL DEFAULT '',
  `password_hash` varchar(255) DEFAULT NULL,
  `auth_source` varchar(20) NOT NULL DEFAULT 'local',
  `external_id` varchar(320) DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `mfa_secret` varchar(500) DEFAULT NULL,
  `mfa_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `mfa_recovery_json` text NOT NULL DEFAULT (_utf8mb4'[]'),
  `failed_logins` int NOT NULL DEFAULT '0',
  `locked_until` datetime(6) DEFAULT NULL,
  `last_login_at` datetime(6) DEFAULT NULL,
  `deactivated_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `home_folder_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_username` (`username`),
  KEY `ix_users_email` (`email`),
  KEY `fk_users_home_folder` (`home_folder_id`),
  CONSTRAINT `fk_users_home_folder` FOREIGN KEY (`home_folder_id`) REFERENCES `folders` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=6 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `users`
--

LOCK TABLES `users` WRITE;
/*!40000 ALTER TABLE `users` DISABLE KEYS */;
INSERT INTO `users` VALUES (2,'awdesh','awdesh@jmbaxi.com','awdesh nonia','scrypt:32768:8:1$beEf0vPlXhEYOoZY$19ad779be6f26e10200927cbbc7fa1c214c7739164dfad186b67bf3fa78c7205d00cef5bbb39447f872b46e258a80b3157e2bde19311bb2b1fa074b9bd00e78a','local',NULL,1,NULL,0,'[]',0,NULL,'2026-11-05 06:59:59.567364','2026-11-02 12:20:13.078807',2),(3,'pks','pawans1@jmbaxi.com','pawan','scrypt:32768:8:1$J7lWer0sDcCvh0MZ$8de8d0052aa96bb285ec843650fa26f4493763ebcb1dc30a08623226a7b3a9ffbc6cab64e26eda9852abb7cbef0a0b23de3f7ab600b9ccc00b873f49556a3b00','local',NULL,1,NULL,0,'[]',0,NULL,'2026-11-03 10:02:12.739090','2026-11-02 12:29:07.513345',3),(4,'pawan','pawans@jmbaxi.com','pawan singh','scrypt:32768:8:1$OBa3ddLB6DBaPpDj$206071650cd2e4111c8e8ae7a3f1a4aedeb55ba2d37d724b9df386e025425301a40d0f74a5cb196ca79581db152150cf9b89ad55eb70f1cd76d37b1aa95574d1','local',NULL,1,NULL,0,'[]',0,NULL,'2026-11-05 10:40:27.992984','2026-11-03 12:39:40.597625',4),(5,'aws','avdeshk@jmbaxi.com','awdesh','scrypt:32768:8:1$dFeU61gUyiAwUuph$bf80b5231f1b0a66a1e381eba04122fbd1c40009336dc2443488b00772060f32db5f2363dcef878f13d3ecd28314cd58df7b59f0d623456bce0a313da923d039','local',NULL,1,NULL,0,'[]',0,NULL,'2026-11-06 05:03:02.963301','2026-11-03 12:42:23.794625',5);
/*!40000 ALTER TABLE `users` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `vocabularies`
--

DROP TABLE IF EXISTS `vocabularies`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `vocabularies` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(120) NOT NULL,
  `description` varchar(500) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_vocabularies_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `vocabularies`
--

LOCK TABLES `vocabularies` WRITE;
/*!40000 ALTER TABLE `vocabularies` DISABLE KEYS */;
/*!40000 ALTER TABLE `vocabularies` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `vocabulary_terms`
--

DROP TABLE IF EXISTS `vocabulary_terms`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `vocabulary_terms` (
  `id` int NOT NULL AUTO_INCREMENT,
  `vocabulary_id` int NOT NULL,
  `value` varchar(200) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_vocabulary_term` (`vocabulary_id`,`value`),
  CONSTRAINT `fk_vocab_terms_vocab` FOREIGN KEY (`vocabulary_id`) REFERENCES `vocabularies` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `vocabulary_terms`
--

LOCK TABLES `vocabulary_terms` WRITE;
/*!40000 ALTER TABLE `vocabulary_terms` DISABLE KEYS */;
/*!40000 ALTER TABLE `vocabulary_terms` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping events for database 'DMS'
--

--
-- Dumping routines for database 'DMS'
--
/*!50003 DROP PROCEDURE IF EXISTS `sp_activity_log_add` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_activity_log_add`(
  IN p_category VARCHAR(20), IN p_action VARCHAR(40), IN p_message VARCHAR(500),
  IN p_entity_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  INSERT INTO activity_log (category, action, message, entity_id, user_id, user_name, ip, created_at)
  VALUES (p_category, p_action, COALESCE(LEFT(p_message, 500), ''), p_entity_id, p_user_id,
          p_user_name, p_ip, UTC_TIMESTAMP(6));
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_admin_stats` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_admin_stats`()
BEGIN
  SELECT
    (SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL)          AS documents,
    (SELECT COUNT(*) FROM folders   WHERE deleted_at IS NULL)          AS folders,
    (SELECT COUNT(*) FROM documents WHERE deleted_at IS NOT NULL)      AS in_trash,
    (SELECT COALESCE(SUM(size), 0) FROM document_versions)             AS storage_bytes,
    (SELECT COUNT(*) FROM archived_emails)                             AS emails,
    (SELECT COUNT(*) FROM users WHERE is_active = 1)                   AS active_users,
    (SELECT COUNT(*) FROM users WHERE locked_until > UTC_TIMESTAMP(6)) AS locked_users;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_add_version` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_add_version`(
  IN p_document_id INT, IN p_is_major TINYINT, IN p_user_id INT, IN p_user_name VARCHAR(200),
  IN p_storage_key VARCHAR(255), IN p_original_filename VARCHAR(255), IN p_mime_type VARCHAR(127),
  IN p_size BIGINT, IN p_sha256 VARCHAR(64), IN p_note VARCHAR(500), IN p_idempotency_key VARCHAR(64),
  IN p_scan_status VARCHAR(20), IN p_scan_engine VARCHAR(40))
proc: BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE v_locked_by INT DEFAULT NULL;
  DECLARE v_next INT DEFAULT 1;
  DECLARE v_major INT DEFAULT NULL;
  DECLARE v_minor INT DEFAULT NULL;
  DECLARE v_ver_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF p_idempotency_key IS NOT NULL THEN
    SELECT id INTO v_ver_id FROM document_versions WHERE idempotency_key = p_idempotency_key LIMIT 1;
    IF v_ver_id IS NOT NULL THEN
      SELECT p_document_id AS document_id, v_ver_id AS version_id, 1 AS was_duplicate;
      LEAVE proc;
    END IF;
  END IF;

  START TRANSACTION;
  -- Row lock: do log ek saath upload karein to version number nahi takrayega
  SELECT name, checked_out_by_id INTO v_name, v_locked_by
  FROM documents WHERE id = p_document_id AND deleted_at IS NULL FOR UPDATE;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document not found.';
  END IF;
  IF v_locked_by IS NOT NULL AND v_locked_by <> COALESCE(p_user_id, 0) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is checked out by another user.';
  END IF;

  SELECT COALESCE(MAX(version_number), 0) + 1 INTO v_next
  FROM document_versions WHERE document_id = p_document_id;
  SELECT COALESCE(v.major, v.version_number), COALESCE(v.minor, 0) INTO v_major, v_minor
  FROM documents d JOIN document_versions v ON v.id = d.current_version_id
  WHERE d.id = p_document_id;
  SET v_major = COALESCE(v_major, 0);
  SET v_minor = COALESCE(v_minor, 0);
  IF p_is_major = 1 THEN
    SET v_major = v_major + 1;
    SET v_minor = 0;
  ELSE
    SET v_minor = v_minor + 1;
  END IF;

  INSERT INTO document_versions (document_id, version_number, major, minor, storage_key, original_filename,
         mime_type, size, sha256, note, idempotency_key, created_by_id, scan_status, scan_engine, created_at)
  VALUES (p_document_id, v_next, v_major, v_minor, p_storage_key, p_original_filename, p_mime_type, p_size,
          p_sha256, p_note, p_idempotency_key, p_user_id, COALESCE(p_scan_status, 'clean'), p_scan_engine, v_now);
  SET v_ver_id = LAST_INSERT_ID();

  UPDATE documents SET current_version_id = v_ver_id, updated_at = v_now WHERE id = p_document_id;
  CALL sp_activity_log_add('document', 'new_version',
       CONCAT('New version ', v_major, '.', v_minor, ' of ''', v_name, ''''),
       p_document_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT p_document_id AS document_id, v_ver_id AS version_id, v_major AS major, v_minor AS minor,
         0 AS was_duplicate;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_checkin` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_checkin`(IN p_document_id INT, IN p_user_id INT, IN p_is_admin TINYINT,
                                     IN p_user_name VARCHAR(200))
BEGIN
  UPDATE documents SET checked_out_by_id = NULL, checked_out_at = NULL
  WHERE id = p_document_id AND checked_out_by_id IS NOT NULL
    AND (checked_out_by_id = p_user_id OR p_is_admin = 1);
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is not checked out by you.';
  END IF;
  CALL sp_activity_log_add('document', 'checkin', 'Checked in', p_document_id, p_user_id, p_user_name, NULL);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_checkout` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_checkout`(IN p_document_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  UPDATE documents SET checked_out_by_id = p_user_id, checked_out_at = UTC_TIMESTAMP(6)
  WHERE id = p_document_id AND deleted_at IS NULL AND checked_out_by_id IS NULL;
  IF ROW_COUNT() = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is already checked out or does not exist.';
  END IF;
  CALL sp_activity_log_add('document', 'checkout', 'Checked out', p_document_id, p_user_id, p_user_name, NULL);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_create` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_create`(
  IN p_folder_id INT, IN p_name VARCHAR(255), IN p_extension VARCHAR(16), IN p_document_type_id INT,
  IN p_user_id INT, IN p_user_name VARCHAR(200),
  IN p_storage_key VARCHAR(255), IN p_original_filename VARCHAR(255), IN p_mime_type VARCHAR(127),
  IN p_size BIGINT, IN p_sha256 VARCHAR(64), IN p_note VARCHAR(500), IN p_idempotency_key VARCHAR(64),
  IN p_scan_status VARCHAR(20), IN p_scan_engine VARCHAR(40))
proc: BEGIN
  DECLARE v_doc_id INT DEFAULT NULL;
  DECLARE v_ver_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  -- Same request dobara aaye (network retry) to wahi document lautao
  IF p_idempotency_key IS NOT NULL THEN
    SELECT document_id, id INTO v_doc_id, v_ver_id
    FROM document_versions WHERE idempotency_key = p_idempotency_key LIMIT 1;
    IF v_ver_id IS NOT NULL THEN
      SELECT v_doc_id AS document_id, v_ver_id AS version_id, 1 AS was_duplicate;
      LEAVE proc;
    END IF;
  END IF;

  START TRANSACTION;
  IF p_folder_id IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM folders WHERE id = p_folder_id AND deleted_at IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The folder does not exist.';
  END IF;
  IF EXISTS (SELECT 1 FROM documents
             WHERE folder_id <=> p_folder_id AND name = p_name AND deleted_at IS NULL) THEN
    SET v_msg = CONCAT('A document named ''', p_name, ''' already exists in this folder.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;

  INSERT INTO documents (name, extension, folder_id, document_type_id, created_by_id, created_at, updated_at)
  VALUES (p_name, COALESCE(p_extension, ''), p_folder_id, p_document_type_id, p_user_id, v_now, v_now);
  SET v_doc_id = LAST_INSERT_ID();

  INSERT INTO document_versions (document_id, version_number, major, minor, storage_key, original_filename,
         mime_type, size, sha256, note, idempotency_key, created_by_id, scan_status, scan_engine, created_at)
  VALUES (v_doc_id, 1, 1, 0, p_storage_key, p_original_filename, p_mime_type, p_size, p_sha256, p_note,
          p_idempotency_key, p_user_id, COALESCE(p_scan_status, 'clean'), p_scan_engine, v_now);
  SET v_ver_id = LAST_INSERT_ID();

  UPDATE documents SET current_version_id = v_ver_id WHERE id = v_doc_id;
  CALL sp_activity_log_add('document', 'upload', CONCAT('Uploaded ''', p_name, ''''),
                           v_doc_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_doc_id AS document_id, v_ver_id AS version_id, 0 AS was_duplicate;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_get` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_get`(IN p_document_id INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.folder_id, d.current_version_id, d.document_type_id,
         d.inherit_permissions, d.checked_out_by_id, d.checked_out_at, d.deleted_at,
         d.created_by_id, d.created_at, d.updated_at,
         f.name AS folder_name,
         v.version_number, v.major, v.minor, v.storage_key, v.original_filename, v.mime_type,
         v.size, v.sha256, v.scan_status,
         cu.display_name AS created_by_name, co.display_name AS checked_out_by_name
  FROM documents d
  LEFT JOIN folders f           ON f.id  = d.folder_id
  LEFT JOIN document_versions v ON v.id  = d.current_version_id
  LEFT JOIN users cu            ON cu.id = d.created_by_id
  LEFT JOIN users co            ON co.id = d.checked_out_by_id
  WHERE d.id = p_document_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_list_versions` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_list_versions`(IN p_document_id INT)
BEGIN
  SELECT v.id, v.version_number, v.major, v.minor, v.original_filename, v.mime_type, v.size,
         v.note, v.scan_status, v.restored_from, v.created_at, u.display_name AS created_by_name
  FROM document_versions v
  LEFT JOIN users u ON u.id = v.created_by_id
  WHERE v.document_id = p_document_id
  ORDER BY v.version_number DESC;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_document_soft_delete` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_document_soft_delete`(IN p_document_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_batch VARCHAR(32) DEFAULT REPLACE(UUID(), '-', '');
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE v_locked_by INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  SELECT name, checked_out_by_id INTO v_name, v_locked_by
  FROM documents WHERE id = p_document_id AND deleted_at IS NULL FOR UPDATE;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document not found.';
  END IF;
  IF v_locked_by IS NOT NULL AND v_locked_by <> COALESCE(p_user_id, 0) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This document is checked out by another user.';
  END IF;
  UPDATE documents SET deleted_at = UTC_TIMESTAMP(6), deleted_batch = v_batch, deleted_by_id = p_user_id
  WHERE id = p_document_id;
  CALL sp_activity_log_add('document', 'delete', CONCAT('Moved ''', v_name, ''' to trash'),
                           p_document_id, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_batch AS deleted_batch;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_email_get_owned` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_email_get_owned`(IN p_user_id INT, IN p_email_id INT)
BEGIN
  -- Email tabhi milti hai jab wo p_user_id ki apni mailbox ki ho.
  SELECT e.id
  FROM archived_emails e
  JOIN gmail_accounts a ON a.id = e.account_id
  WHERE e.id = p_email_id AND a.user_id = p_user_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_email_list` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_email_list`(
  IN p_user_id INT, IN p_q VARCHAR(200), IN p_state VARCHAR(20), IN p_with_attachments TINYINT,
  IN p_sort VARCHAR(20), IN p_limit INT, IN p_offset INT)
BEGIN
  -- Sirf p_user_id ki apni mailboxes ki emails. Har row: total + us page ki email id (khali page par id NULL).
  DECLARE v_q    VARCHAR(200) DEFAULT TRIM(COALESCE(p_q, ''));
  DECLARE v_like VARCHAR(210) DEFAULT CONCAT('%', TRIM(COALESCE(p_q, '')), '%');
  DECLARE v_sort VARCHAR(20)  DEFAULT COALESCE(p_sort, 'imported');
  WITH f AS (
    SELECT e.id, e.subject, e.sender, e.received_at, e.imported_at
    FROM archived_emails e
    JOIN gmail_accounts a ON a.id = e.account_id
    WHERE a.user_id = p_user_id
      AND (v_q = '' OR e.subject LIKE v_like OR e.sender LIKE v_like
           OR e.recipients LIKE v_like OR e.snippet LIKE v_like)
      AND (COALESCE(p_state, '') = '' OR e.gmail_state = p_state)
      AND (COALESCE(p_with_attachments, 0) = 0 OR e.attachment_count > 0)
  ), ranked AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY
             CASE WHEN v_sort = 'subject'  THEN LOWER(subject) END ASC,
             CASE WHEN v_sort = 'sender'   THEN LOWER(sender)  END ASC,
             CASE WHEN v_sort = 'received' THEN received_at    END DESC,
             CASE WHEN v_sort NOT IN ('subject', 'sender', 'received') THEN imported_at END DESC,
             id DESC) AS rn
    FROM f
  )
  SELECT t.total, r.id
  FROM (SELECT COUNT(*) AS total FROM f) t
  LEFT JOIN ranked r ON r.rn > p_offset AND r.rn <= p_offset + p_limit
  ORDER BY r.rn;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_favourite_toggle` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_favourite_toggle`(IN p_user_id INT, IN p_target_type VARCHAR(10), IN p_target_id INT)
BEGIN
  DELETE FROM favourites
  WHERE user_id = p_user_id AND target_type = p_target_type AND target_id = p_target_id;
  IF ROW_COUNT() = 0 THEN
    INSERT INTO favourites (user_id, target_type, target_id, created_at)
    VALUES (p_user_id, p_target_type, p_target_id, UTC_TIMESTAMP(6));
    SELECT 1 AS is_favourite;
  ELSE
    SELECT 0 AS is_favourite;
  END IF;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_folder_create` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_folder_create`(
  IN p_name VARCHAR(255), IN p_parent_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT TRIM(COALESCE(p_name, ''));
  DECLARE v_id INT DEFAULT NULL;
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_name = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter a folder name.';
  END IF;

  START TRANSACTION;
  IF p_parent_id IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM folders WHERE id = p_parent_id AND deleted_at IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The parent folder does not exist.';
  END IF;
  IF EXISTS (SELECT 1 FROM folders
             WHERE parent_id <=> p_parent_id AND name = v_name AND deleted_at IS NULL) THEN
    SET v_msg = CONCAT('A folder named ''', v_name, ''' already exists here.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;

  INSERT INTO folders (name, parent_id, created_by_id, created_at, updated_at)
  VALUES (v_name, p_parent_id, p_user_id, v_now, v_now);
  SET v_id = LAST_INSERT_ID();
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (v_id, v_name, 'created', JSON_OBJECT('parent_id', p_parent_id), p_user_name, v_now);
  COMMIT;
  SELECT v_id AS folder_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_folder_list_documents` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_folder_list_documents`(IN p_folder_id INT, IN p_limit INT, IN p_offset INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.document_type_id, d.created_at, d.updated_at,
         d.checked_out_by_id, d.checked_out_at,
         v.id AS version_id, v.major, v.minor, v.size, v.mime_type, v.scan_status,
         u.display_name AS created_by_name
  FROM documents d
  LEFT JOIN document_versions v ON v.id = d.current_version_id
  LEFT JOIN users u ON u.id = d.created_by_id
  WHERE d.folder_id <=> p_folder_id AND d.deleted_at IS NULL
  ORDER BY d.name
  LIMIT p_limit OFFSET p_offset;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_folder_list_subfolders` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_folder_list_subfolders`(IN p_parent_id INT)
BEGIN
  SELECT f.id, f.name, f.inherit_permissions, f.created_at, f.updated_at,
         (SELECT COUNT(*) FROM folders c WHERE c.parent_id = f.id AND c.deleted_at IS NULL) AS sub_count
  FROM folders f
  WHERE f.parent_id <=> p_parent_id AND f.deleted_at IS NULL
  ORDER BY f.name;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_folder_rename` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_folder_rename`(IN p_folder_id INT, IN p_new_name VARCHAR(255), IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_name VARCHAR(255) DEFAULT TRIM(COALESCE(p_new_name, ''));
  DECLARE v_old VARCHAR(255) DEFAULT NULL;
  DECLARE v_parent INT DEFAULT NULL;
  DECLARE v_msg VARCHAR(500);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_name = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Enter a folder name.';
  END IF;

  START TRANSACTION;
  SELECT name, parent_id INTO v_old, v_parent
  FROM folders WHERE id = p_folder_id AND deleted_at IS NULL FOR UPDATE;
  IF v_old IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Folder not found.';
  END IF;
  IF EXISTS (SELECT 1 FROM folders WHERE parent_id <=> v_parent AND name = v_name
             AND deleted_at IS NULL AND id <> p_folder_id) THEN
    SET v_msg = CONCAT('A folder named ''', v_name, ''' already exists here.');
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = v_msg;
  END IF;
  UPDATE folders SET name = v_name, updated_at = UTC_TIMESTAMP(6) WHERE id = p_folder_id;
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (p_folder_id, v_name, 'renamed', JSON_OBJECT('from', v_old, 'to', v_name), p_user_name, UTC_TIMESTAMP(6));
  COMMIT;
  SELECT p_folder_id AS folder_id, v_name AS name;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_folder_soft_delete` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_folder_soft_delete`(IN p_folder_id INT, IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_batch VARCHAR(32) DEFAULT REPLACE(UUID(), '-', '');
  DECLARE v_now DATETIME(6) DEFAULT UTC_TIMESTAMP(6);
  DECLARE v_name VARCHAR(255) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
    RESIGNAL;
  END;

  SELECT name INTO v_name FROM folders WHERE id = p_folder_id AND deleted_at IS NULL;
  IF v_name IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Folder not found.';
  END IF;

  DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
  CREATE TEMPORARY TABLE tmp_del_folders (id INT PRIMARY KEY);
  INSERT INTO tmp_del_folders (id)
    WITH RECURSIVE tree AS (
      SELECT id FROM folders WHERE id = p_folder_id
      UNION ALL
      SELECT c.id FROM folders c JOIN tree t ON c.parent_id = t.id WHERE c.deleted_at IS NULL
    )
    SELECT id FROM tree;

  IF EXISTS (SELECT 1 FROM documents d JOIN tmp_del_folders t ON t.id = d.folder_id
             WHERE d.deleted_at IS NULL AND d.checked_out_by_id IS NOT NULL
               AND d.checked_out_by_id <> COALESCE(p_user_id, 0)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A document in this folder is checked out by another user.';
  END IF;

  START TRANSACTION;
  UPDATE folders f JOIN tmp_del_folders t ON t.id = f.id
     SET f.deleted_at = v_now, f.deleted_batch = v_batch, f.deleted_by_id = p_user_id;
  UPDATE documents d JOIN tmp_del_folders t ON t.id = d.folder_id
     SET d.deleted_at = v_now, d.deleted_batch = v_batch, d.deleted_by_id = p_user_id
   WHERE d.deleted_at IS NULL;
  INSERT INTO folder_activity (folder_id, folder_name, action, details_json, user_name, created_at)
  VALUES (p_folder_id, v_name, 'deleted', JSON_OBJECT('batch', v_batch), p_user_name, v_now);
  COMMIT;

  DROP TEMPORARY TABLE IF EXISTS tmp_del_folders;
  SELECT v_batch AS deleted_batch;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_accounts_admin_list` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_accounts_admin_list`(IN p_limit INT, IN p_offset INT)
BEGIN
  -- Admin ke liye sirf mailbox ka haal (kaun, kab sync, errors). Email ka content yahan nahi aata.
  SELECT a.id AS account_id, a.email, a.user_id, u.username, a.is_active, a.connected_at, a.last_sync_at,
         a.last_success_at, a.last_sync_status, a.last_error,
         (SELECT COUNT(*) FROM archived_emails e WHERE e.account_id = a.id) AS archived_total,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'pending') AS pending,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'failed')  AS failed,
         COUNT(*) OVER () AS total
  FROM gmail_accounts a
  LEFT JOIN users u ON u.id = a.user_id
  ORDER BY a.email
  LIMIT p_limit OFFSET p_offset;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_accounts_due` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_accounts_due`(IN p_limit INT)
BEGIN
  -- Sync ke liye taiyaar mailboxes, jo sabse pehle sync hui thi wo pehle.
  -- reauth_required wali tab tak ruki rehti hain jab tak user dobara connect na kare.
  SELECT id FROM gmail_accounts
  WHERE is_active = 1 AND user_id IS NOT NULL AND token_enc IS NOT NULL
    AND last_sync_status <> 'reauth_required'
    AND (lock_until IS NULL OR lock_until < UTC_TIMESTAMP(6))
  ORDER BY last_sync_at IS NOT NULL, last_sync_at, id
  LIMIT p_limit;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_account_connect` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_account_connect`(
  IN p_user_id INT, IN p_email VARCHAR(320), IN p_token_enc TEXT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_email VARCHAR(320) DEFAULT LOWER(TRIM(COALESCE(p_email, '')));
  DECLARE v_id    INT DEFAULT NULL;
  DECLARE v_owner INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF p_user_id IS NULL OR v_email = '' OR COALESCE(p_token_enc, '') = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'A user, a Gmail address and an authorization are required.';
  END IF;

  START TRANSACTION;
  SELECT id, user_id INTO v_id, v_owner FROM gmail_accounts WHERE email = v_email FOR UPDATE;
  IF v_owner IS NOT NULL AND v_owner <> p_user_id THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This Gmail account is already connected by another DMS user.';
  END IF;

  -- Ek user ki ek hi mailbox sync hoti hai. Pehle wali band hoti hai, par uski emails usi user ki rehti hain.
  UPDATE gmail_accounts
     SET is_active = 0, token_enc = NULL, last_sync_status = 'disconnected'
   WHERE user_id = p_user_id AND is_active = 1 AND email <> v_email;

  IF v_id IS NULL THEN
    INSERT INTO gmail_accounts (email, user_id, token_enc, history_id, is_active, connected_at,
                                last_sync_status, legacy_imported)
    VALUES (v_email, p_user_id, p_token_enc, NULL, 1, UTC_TIMESTAMP(6), 'never', 1);
    SET v_id = LAST_INSERT_ID();
  ELSE
    -- Wahi mailbox dobara connect hui: history_id rehta hai, sync wahin se aage chalega.
    UPDATE gmail_accounts
       SET user_id = p_user_id, token_enc = p_token_enc, is_active = 1, connected_at = UTC_TIMESTAMP(6),
           last_sync_status = IF(history_id IS NULL, 'never', 'connected'), last_error = NULL,
           legacy_imported = 1
     WHERE id = v_id;
  END IF;
  CALL sp_activity_log_add('gmail', 'connected', CONCAT('Gmail account ', v_email, ' connected'),
                           v_id, p_user_id, p_user_name, p_ip);
  COMMIT;
  SELECT v_id AS account_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_account_disconnect` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_account_disconnect`(IN p_user_id INT, IN p_user_name VARCHAR(200), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_count  INT DEFAULT 0;
  DECLARE v_emails VARCHAR(2000) DEFAULT NULL;
  SELECT COUNT(*), GROUP_CONCAT(email ORDER BY id SEPARATOR ', ') INTO v_count, v_emails
    FROM gmail_accounts WHERE user_id = p_user_id AND is_active = 1;
  -- Token hat jaata hai; archived emails DMS mein usi user ki rehti hain.
  UPDATE gmail_accounts SET is_active = 0, token_enc = NULL, last_sync_status = 'disconnected'
   WHERE user_id = p_user_id AND is_active = 1;
  IF v_count > 0 THEN
    CALL sp_activity_log_add('gmail', 'disconnected', CONCAT('Gmail account ', v_emails, ' disconnected'),
                             NULL, p_user_id, p_user_name, p_ip);
  END IF;
  SELECT v_count AS disconnected;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_account_save_token` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_account_save_token`(IN p_account_id INT, IN p_token_enc TEXT)
BEGIN
  -- Refresh ke baad naya token. Beech mein disconnect hua ho to token wapas nahi likha jaata.
  UPDATE gmail_accounts SET token_enc = p_token_enc
   WHERE id = p_account_id AND is_active = 1 AND token_enc IS NOT NULL;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_retry_failed` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_retry_failed`(IN p_user_id INT)
BEGIN
  -- p_user_id NULL = sabki mailboxes (admin); warna sirf us user ki.
  UPDATE pending_imports p
    JOIN gmail_accounts a ON a.id = p.account_id
     SET p.status = 'pending', p.attempts = 0
   WHERE p.status = 'failed' AND (p_user_id IS NULL OR a.user_id = p_user_id);
  SELECT ROW_COUNT() AS requeued;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_gmail_status_for_user` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_gmail_status_for_user`(IN p_user_id INT)
BEGIN
  -- User ki mailbox (chalu wali, warna sabse nayi) aur uski apni emails ki ginti.
  SELECT a.id AS account_id, a.email, a.is_active, a.connected_at, a.last_sync_at, a.last_success_at,
         a.last_sync_status, a.last_error,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'pending') AS pending,
         (SELECT COUNT(*) FROM pending_imports p WHERE p.account_id = a.id AND p.status = 'failed')  AS failed,
         (SELECT COUNT(*) FROM archived_emails e JOIN gmail_accounts x ON x.id = e.account_id
           WHERE x.user_id = p_user_id) AS archived_total
  FROM gmail_accounts a
  WHERE a.user_id = p_user_id
  ORDER BY a.is_active DESC, a.connected_at DESC, a.id DESC
  LIMIT 1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_recent_list` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_recent_list`(IN p_user_id INT, IN p_limit INT)
BEGIN
  SELECT d.id, d.name, d.extension, d.folder_id, r.viewed_at
  FROM recent_items r JOIN documents d ON d.id = r.document_id
  WHERE r.user_id = p_user_id AND d.deleted_at IS NULL
  ORDER BY r.viewed_at DESC
  LIMIT p_limit;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_recent_touch` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_recent_touch`(IN p_user_id INT, IN p_document_id INT)
BEGIN
  INSERT INTO recent_items (user_id, document_id, viewed_at)
  VALUES (p_user_id, p_document_id, UTC_TIMESTAMP(6)) AS new
  ON DUPLICATE KEY UPDATE viewed_at = new.viewed_at;

  DELETE FROM recent_items
  WHERE user_id = p_user_id
    AND viewed_at < (SELECT x.viewed_at FROM (
          SELECT viewed_at FROM recent_items WHERE user_id = p_user_id
          ORDER BY viewed_at DESC LIMIT 1 OFFSET 49) x);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_search` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_search`(IN p_query VARCHAR(500), IN p_kind VARCHAR(10), IN p_limit INT, IN p_offset INT)
BEGIN
  SELECT e.kind, e.ref_id, LEFT(e.title, 300) AS title, LEFT(e.body, 300) AS snippet,
         MATCH(e.title, e.body, e.meta) AGAINST (p_query IN BOOLEAN MODE) AS score
  FROM search_entries e
  WHERE MATCH(e.title, e.body, e.meta) AGAINST (p_query IN BOOLEAN MODE)
    AND (p_kind IS NULL OR e.kind = p_kind)
  ORDER BY score DESC
  LIMIT p_limit OFFSET p_offset;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_search_delete` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_search_delete`(IN p_kind VARCHAR(10), IN p_ref_id INT)
BEGIN
  DELETE FROM search_entries WHERE kind = p_kind AND ref_id = p_ref_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_search_upsert` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_search_upsert`(IN p_kind VARCHAR(10), IN p_ref_id INT,
                                  IN p_title TEXT, IN p_body MEDIUMTEXT, IN p_meta TEXT)
BEGIN
  INSERT INTO search_entries (kind, ref_id, title, body, meta, updated_at)
  VALUES (p_kind, p_ref_id, COALESCE(p_title, ''), COALESCE(p_body, ''), COALESCE(p_meta, ''), UTC_TIMESTAMP(6))
  AS new
  ON DUPLICATE KEY UPDATE title = new.title, body = new.body, meta = new.meta, updated_at = new.updated_at;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_trash_restore` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_trash_restore`(IN p_batch VARCHAR(32), IN p_user_id INT, IN p_user_name VARCHAR(200))
BEGIN
  DECLARE v_folders INT DEFAULT 0;
  DECLARE v_docs INT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  UPDATE folders SET deleted_at = NULL, deleted_batch = NULL, deleted_by_id = NULL
  WHERE deleted_batch = p_batch;
  SET v_folders = ROW_COUNT();
  UPDATE documents SET deleted_at = NULL, deleted_batch = NULL, deleted_by_id = NULL
  WHERE deleted_batch = p_batch;
  SET v_docs = ROW_COUNT();
  IF v_folders + v_docs = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Nothing to restore for this item.';
  END IF;
  CALL sp_activity_log_add('document', 'restore',
       CONCAT('Restored ', v_folders, ' folder(s) and ', v_docs, ' document(s) from trash'),
       NULL, p_user_id, p_user_name, NULL);
  COMMIT;
  SELECT v_folders AS folders_restored, v_docs AS documents_restored;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_create` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_create`(
  IN p_username VARCHAR(150), IN p_email VARCHAR(320), IN p_display_name VARCHAR(200),
  IN p_password_hash VARCHAR(255), IN p_auth_source VARCHAR(20), IN p_role_name VARCHAR(80))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT LOWER(TRIM(COALESCE(p_username, '')));
  DECLARE v_email    VARCHAR(320) DEFAULT NULLIF(LOWER(TRIM(COALESCE(p_email, ''))), '');
  DECLARE v_role_id  INT DEFAULT NULL;
  DECLARE v_user_id  INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  IF v_username = '' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Choose a user name.';
  END IF;
  -- p_role_name NULL = koi role nahi
  IF p_role_name IS NOT NULL THEN
    SELECT id INTO v_role_id FROM roles WHERE name = p_role_name LIMIT 1;
    IF v_role_id IS NULL THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The requested role is unavailable.';
    END IF;
  END IF;

  START TRANSACTION;
  IF EXISTS (SELECT 1 FROM users
             WHERE username IN (v_username, v_email) OR email IN (v_username, v_email)) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'An account with this user name or email already exists.';
  END IF;
  INSERT INTO users (username, email, display_name, password_hash, auth_source, is_active, mfa_enabled,
                     mfa_recovery_json, failed_logins, created_at)
  VALUES (v_username, v_email, LEFT(COALESCE(p_display_name, ''), 200), p_password_hash,
          COALESCE(p_auth_source, 'local'), 1, 0, '[]', 0, UTC_TIMESTAMP(6));
  SET v_user_id = LAST_INSERT_ID();
  IF v_role_id IS NOT NULL THEN
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_id);
  END IF;
  COMMIT;
  SELECT v_user_id AS user_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_find_by_login` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_find_by_login`(IN p_login VARCHAR(320))
BEGIN
  DECLARE v_login VARCHAR(320) DEFAULT LOWER(TRIM(COALESCE(p_login, '')));
  SELECT id, username, email, display_name, password_hash, auth_source, is_active,
         mfa_enabled, failed_logins, locked_until, last_login_at, home_folder_id
  FROM users
  WHERE v_login <> '' AND (username = v_login OR email = v_login)
  LIMIT 1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_get_roles` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_get_roles`(IN p_user_id INT)
BEGIN
  SELECT r.id, r.name, r.capabilities, r.is_system
  FROM roles r JOIN user_roles ur ON ur.role_id = r.id
  WHERE ur.user_id = p_user_id
  ORDER BY r.name;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_login_failed` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_login_failed`(IN p_user_id INT, IN p_threshold INT, IN p_lock_minutes INT,
                                      IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_failed INT DEFAULT NULL;
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION BEGIN ROLLBACK; RESIGNAL; END;

  START TRANSACTION;
  SELECT failed_logins + 1, username INTO v_failed, v_username
  FROM users WHERE id = p_user_id FOR UPDATE;
  IF v_failed IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  IF v_failed >= p_threshold THEN
    UPDATE users SET failed_logins = 0,
                     locked_until = UTC_TIMESTAMP(6) + INTERVAL p_lock_minutes MINUTE
    WHERE id = p_user_id;
    CALL sp_activity_log_add('security', 'account_locked',
         CONCAT('Account ''', v_username, ''' locked after failed sign-ins'), p_user_id, NULL, NULL, p_ip);
  ELSE
    UPDATE users SET failed_logins = v_failed WHERE id = p_user_id;
  END IF;
  COMMIT;
  SELECT failed_logins, locked_until FROM users WHERE id = p_user_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_login_success` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_login_success`(IN p_user_id INT, IN p_method VARCHAR(20), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE v_label VARCHAR(200) DEFAULT NULL;
  UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = UTC_TIMESTAMP(6)
  WHERE id = p_user_id;
  SELECT username, COALESCE(NULLIF(display_name, ''), username) INTO v_username, v_label
  FROM users WHERE id = p_user_id;
  IF v_username IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  CALL sp_activity_log_add('security', 'login',
       CONCAT('''', v_username, ''' signed in (', COALESCE(p_method, 'password'), ')'),
       p_user_id, p_user_id, v_label, p_ip);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 DROP PROCEDURE IF EXISTS `sp_user_set_password` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
CREATE PROCEDURE `sp_user_set_password`(IN p_user_id INT, IN p_password_hash VARCHAR(255), IN p_ip VARCHAR(64))
BEGIN
  DECLARE v_username VARCHAR(150) DEFAULT NULL;
  DECLARE v_label VARCHAR(200) DEFAULT NULL;
  SELECT username, COALESCE(NULLIF(display_name, ''), username) INTO v_username, v_label
  FROM users WHERE id = p_user_id;
  IF v_username IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'User not found.';
  END IF;
  UPDATE users SET password_hash = p_password_hash, failed_logins = 0, locked_until = NULL
  WHERE id = p_user_id;
  CALL sp_activity_log_add('security', 'password_changed',
       CONCAT('''', v_username, ''' changed their password'), p_user_id, p_user_id, v_label, p_ip);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-11-11 13:10:41
